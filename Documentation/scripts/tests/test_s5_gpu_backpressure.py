"""CPU regression for SDL submission ordering and acquired-fence ownership.

Compile the production submit/finish bodies against a small SDL boundary fake. The
full story replay independently exercises the actual backend and driver; this test
makes the failure paths deterministic without a GPU or installed SDL library.
"""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "host/d3d9gpu/d9g_gpu_sdl.cpp"

FAKE = r"""#include <array>
#include <string>
#include <vector>
#include <unordered_map>
#include <algorithm>
#include <cassert>
#include <cstdio>
#include <cstdint>
enum class GFmt {bgra8, d24s8};
enum class TexType : uint8_t {flat, cube, volume};
struct TexDesc {uint32_t w=1,h=1,levels=1;GFmt fmt=GFmt::bgra8;bool target=false,sampled=false;TexType type=TexType::flat;uint32_t depth=1;};
struct SDL_GPUTexture {};
struct SDL_GPUTextureCreateInfo {uint32_t type=0,format=0,width=0,height=0,layer_count_or_depth=0,num_levels=0,sample_count=0,usage=0;};
constexpr uint32_t SDL_GPU_TEXTURETYPE_2D=1,SDL_GPU_TEXTURETYPE_CUBE=2,SDL_GPU_TEXTURETYPE_3D=3,SDL_GPU_SAMPLECOUNT_1=1,SDL_GPU_TEXTUREFORMAT_INVALID=0;
constexpr uint32_t SDL_GPU_TEXTUREUSAGE_DEPTH_STENCIL_TARGET=1,SDL_GPU_TEXTUREUSAGE_SAMPLER=2,SDL_GPU_TEXTUREUSAGE_COLOR_TARGET=4;
bool is_depth(GFmt f){return f==GFmt::d24s8;}uint32_t sdl_format(GFmt){return 1;}
uint32_t level_dim(uint32_t v,uint32_t l){return std::max(v>>l,1u);}
size_t gfmt_level_bytes(GFmt,uint32_t w,uint32_t h){return size_t(w)*h*4;}
struct ClearValue {bool colour=false,depth=false,stencil=false;};
struct SDL_GPUFence {int id;};struct SDL_GPUCommandBuffer {int id;};struct SDL_GPUDevice {};struct SDL_GPUTransferBuffer {};
std::vector<std::string> ops;bool supports_texture=true;SDL_GPUTexture texture;
bool SDL_GPUTextureSupportsFormat(SDL_GPUDevice*,uint32_t,uint32_t,uint32_t){return supports_texture;}
SDL_GPUTexture* SDL_CreateGPUTexture(SDL_GPUDevice*,const SDL_GPUTextureCreateInfo*){ops.push_back("create");return &texture;}
const char* SDL_GetError(){return "fake SDL failure";}
SDL_GPUFence fences[32];bool fail_wait=false,fail_submit=false,fail_idle=false,wait_error=false,idle_error=false,submit_error=false;int error=0;
void SDL_ClearError(){error=0;}
void SDL_UnmapGPUTransferBuffer(SDL_GPUDevice*,SDL_GPUTransferBuffer*){}
bool SDL_WaitForGPUFences(SDL_GPUDevice*,bool,SDL_GPUFence*const*f,uint32_t n){assert(n==1);ops.push_back("wait"+std::to_string((*f)->id));error=wait_error;return !fail_wait;}
void SDL_ReleaseGPUFence(SDL_GPUDevice*,SDL_GPUFence*f){ops.push_back("release"+std::to_string(f->id));}
SDL_GPUFence* SDL_SubmitGPUCommandBufferAndAcquireFence(SDL_GPUCommandBuffer*cb){ops.push_back("submit"+std::to_string(cb->id));if(fail_submit)return nullptr;error=submit_error;fences[cb->id].id=cb->id;return &fences[cb->id];}
bool SDL_WaitForGPUIdle(SDL_GPUDevice*){ops.push_back("idle");error=idle_error;return !fail_idle;}
struct Backend {
 SDL_GPUDevice*dev_=nullptr;SDL_GPUCommandBuffer*cb_=nullptr;std::array<SDL_GPUFence*,2>pending_{};uint32_t pending_count_=0;
 bool swapchain_acquired_=false;void*ring_map_=nullptr;SDL_GPUTransferBuffer*ring_=nullptr;std::string err_;bool poisoned_=false;
 struct {unsigned waits=0,submits=0;}c_;
 struct Tex {SDL_GPUTexture*t;TexDesc d;uint32_t usage;};
 std::unordered_map<uint32_t,Tex>tex_;uint32_t next_=1;std::vector<uint8_t>zeros_;
 bool clear(uint32_t,uint32_t,const ClearValue&){ops.push_back("clear");return true;}
 bool upload_level(uint32_t,uint32_t,const uint8_t*,size_t){ops.push_back("upload");return true;}
 bool clear_layer(uint32_t,uint32_t,uint32_t,const ClearValue&){ops.push_back("clear");return true;}
 bool upload_subresource(uint32_t,uint32_t,uint32_t,const uint8_t*,size_t){ops.push_back("upload");return true;}
 size_t level_upload_bytes(const TexDesc&d,uint32_t l){return gfmt_level_bytes(d.fmt,level_dim(d.w,l),level_dim(d.h,l));}
 void release_texture(uint32_t id){tex_.erase(id);}

 bool end_render(){return !poisoned_;}bool end_copy(){return !poisoned_;}void release_up_chunks(){}
 template<class F>bool encode_call(const char*,F f){f();return !error;}
 bool check_encoding_error(const char*){if(error)discard_failed_commands();return !error;}void discard_failed_commands(){poisoned_=true;cb_=nullptr;}
"""
CHECKS = r"""};
void expect(std::initializer_list<const char*> literal){std::vector<std::string>v;for(auto s:literal)v.emplace_back(s);assert(ops==v);ops.clear();}
int main(){
 Backend b;SDL_GPUCommandBuffer one{1},two{2},three{3},four{4},five{5};
 b.cb_=&one;assert(b.submit(false));b.cb_=&two;assert(b.submit(false));
 expect({"submit1","submit2"});assert(b.pending_count_==2);
 b.cb_=&three;assert(b.submit(false));expect({"wait1","release1","submit3"});assert(b.pending_count_==2);
 b.cb_=&four;assert(b.submit(true));expect({"wait2","release2","submit4","wait4","release3","release4"});assert(b.pending_count_==0);
 b.cb_=&five;assert(b.submit(false));expect({"submit5"});assert(b.finish());expect({"idle","release5"});assert(b.pending_count_==0);
 b.cb_=&one;assert(b.submit(false));b.cb_=&two;assert(b.submit(false));ops.clear();
 fail_wait=true;b.cb_=&three;assert(!b.submit(false));expect({"wait1"});assert(b.poisoned_ && b.pending_count_==2 && b.c_.submits==7);
 fail_wait=false;assert(!b.finish());expect({"idle","release1","release2"});assert(b.pending_count_==0);
 Backend c;fail_submit=true;c.cb_=&one;assert(!c.submit(false));expect({"submit1"});assert(c.pending_count_==0 && c.poisoned_);

 fail_submit=false;Backend d;d.cb_=&one;assert(d.submit(false));ops.clear();
 fail_idle=true;assert(!d.finish());expect({"idle"});assert(d.pending_count_==1);
 fail_idle=false;idle_error=true;assert(!d.finish());expect({"idle"});assert(d.pending_count_==1);
 idle_error=false;assert(!d.finish());expect({"idle","release1"});assert(d.pending_count_==0);
 Backend e;fail_wait=true;e.cb_=&one;assert(!e.submit(true));expect({"submit1","wait1"});assert(e.pending_count_==1);
 fail_wait=false;assert(!e.finish());expect({"idle","release1"});
 Backend f;wait_error=true;f.cb_=&one;assert(!f.submit(true));expect({"submit1","wait1"});assert(f.pending_count_==1);
 wait_error=false;assert(!f.finish());expect({"idle","release1"});
 Backend g;submit_error=true;g.cb_=&one;assert(!g.submit(false));expect({"submit1"});assert(g.pending_count_==1);
 submit_error=false;assert(!g.finish());expect({"idle","release1"});

 Backend alloc;alloc.cb_=&one;assert(alloc.create_texture(TexDesc{})==1);
 expect({"submit1","create","upload"});assert(alloc.pending_count_==1);
 assert(alloc.finish());expect({"idle","release1"});
 Backend empty;assert(empty.create_texture(TexDesc{})==1);expect({"create","upload"});
 Backend blocked;blocked.cb_=&one;assert(blocked.submit(false));blocked.cb_=&two;assert(blocked.submit(false));ops.clear();
 blocked.cb_=&three;fail_wait=true;assert(blocked.create_texture(TexDesc{})==0);
 expect({"wait1"});assert(blocked.pending_count_==2 && blocked.poisoned_);fail_wait=false;
 assert(!blocked.finish());expect({"idle","release1","release2"});
 Backend failed_alloc;failed_alloc.cb_=&one;fail_submit=true;
 assert(failed_alloc.create_texture(TexDesc{})==0);expect({"submit1"});assert(failed_alloc.tex_.empty());
 fail_submit=false;
 Backend invalid;invalid.cb_=&one;TexDesc bad;bad.w=0;
 assert(invalid.create_texture(bad)==0);expect({});assert(invalid.cb_==&one && invalid.c_.submits==0);
 supports_texture=false;assert(invalid.create_texture(TexDesc{})==0);expect({});assert(invalid.cb_==&one);
 supports_texture=true;
 puts("BACKPRESSURE_FIFO_AND_FAILURE_OK");
}
"""


def harness(source: str) -> str:
    first = source.index("    void release_pending_fences()")
    last = source.index("    SDL_GPUDevice* dev_;", first)
    finish = source.index("    bool finish() override")
    after_finish = source.index("    bool attach_window", finish)
    allocation = source.index("    uint32_t create_texture(")
    after_allocation = source.index("    void release_texture(", allocation)
    return (FAKE + source[allocation:after_allocation].replace(" override", "") + source[first:last]
            + source[finish:after_finish].replace(" override", "") + CHECKS)


def test_gpu_submission_bounds_pending_work_and_retains_fences_until_completed(tmp_path):
    source = tmp_path / "backpressure.cpp"
    source.write_text(harness(SOURCE.read_text()))
    binary = tmp_path / "backpressure"
    built = subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-o", str(binary), str(source)],
                           capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout + built.stderr
    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout == "BACKPRESSURE_FIFO_AND_FAILURE_OK\n"
