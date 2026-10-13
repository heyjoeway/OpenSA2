"""Authored CPU children exercise live mapping and collection without a Vulkan driver."""
from pathlib import Path
import copy
import importlib.util
import json
import os
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

runtime = load("validation_runtime_test", ROOT / "host/d3d9gpu/test/validation_run.py")
validation = load("validation_runtime_helper_test", ROOT / "tools/s5/validation_tools.py")

LAYER_C = r"""#include <vulkan/vulkan.h>
#include <stdlib.h>
#include <string.h>
VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceLayerProperties(uint32_t* count, VkLayerProperties* properties) {
  if (!properties) { *count = 1; return VK_SUCCESS; }
  if (*count < 1) return VK_INCOMPLETE;
  memset(properties, 0, sizeof *properties);
  strcpy(properties->layerName, "VK_LAYER_KHRONOS_validation");
  properties->specVersion = VK_MAKE_API_VERSION(0, 1, 4, getenv("FAKE_OLD_API") ? 309 : 350);
  *count = 1; return VK_SUCCESS;
}
"""
CHILD_C = r"""#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static int instance_key, device_key;
static VkInstanceCreateInfo* expected_instance_info;
static VkDeviceCreateInfo* expected_device_info;
static VkAllocationCallbacks allocator;
static int fail_create;
static VKAPI_ATTR VkResult VKAPI_CALL create_instance(const VkInstanceCreateInfo* info,
    const VkAllocationCallbacks* alloc, VkInstance* result) {
  if (info != expected_instance_info || alloc != &allocator || info->flags != 7) _exit(51);
  if (fail_create) return VK_ERROR_DEVICE_LOST;
  void** p = malloc(sizeof *p); *p = &instance_key; *result = (VkInstance)p; return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL destroy_instance(VkInstance instance, const VkAllocationCallbacks* alloc) {
  if (alloc != &allocator) _exit(52);
  free(instance);
}
static VKAPI_ATTR VkResult VKAPI_CALL create_device(VkPhysicalDevice physical, const VkDeviceCreateInfo* info,
    const VkAllocationCallbacks* alloc, VkDevice* result) {
  (void)physical;
  if (info != expected_device_info || alloc != &allocator || info->flags != 11) _exit(53);
  void** p = malloc(sizeof *p); *p = &device_key; *result = (VkDevice)p; return VK_SUCCESS;
}
static VKAPI_ATTR void VKAPI_CALL destroy_device(VkDevice device, const VkAllocationCallbacks* alloc) {
  if (alloc != &allocator) _exit(54);
  free(device);
}
static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL gipa(VkInstance instance, const char* name) {
  if (!strcmp(name, "vkCreateInstance")) return (PFN_vkVoidFunction)create_instance;
  if (!strcmp(name, "vkDestroyInstance")) return (PFN_vkVoidFunction)destroy_instance;
  if (instance && !strcmp(name, "vkCreateDevice")) return (PFN_vkVoidFunction)create_device;
  return NULL;
}
static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL gdpa(VkDevice device, const char* name) {
  (void)device;
  if (!strcmp(name, "vkDestroyDevice")) return (PFN_vkVoidFunction)destroy_device;
  return NULL;
}
int main(int argc, char** argv) {
  if (argc != 3) return 2;
  const char* layer_path = getenv("FAKE_LAYER_PATH") ? getenv("FAKE_LAYER_PATH") : argv[1];
  const char* probe_path = getenv("FAKE_PROBE_PATH") ? getenv("FAKE_PROBE_PATH") : argv[2];
  if (!getenv("FAKE_MISSING_LAYER") && !dlopen(layer_path, RTLD_NOW | RTLD_LOCAL)) return 3;
  if (getenv("FAKE_NO_PROBE")) return 0;
  void* probe = dlopen(probe_path, RTLD_NOW | RTLD_LOCAL);
  if (!probe) return 4;
  PFN_vkNegotiateLoaderLayerInterfaceVersion negotiate = dlsym(probe, "vkNegotiateLoaderLayerInterfaceVersion");
  VkNegotiateLayerInterface interface = {.sType=LAYER_NEGOTIATE_INTERFACE_STRUCT, .loaderLayerInterfaceVersion=2};
  if (!negotiate || negotiate(&interface) != VK_SUCCESS) return 5;
  for (int i = 0; i < 2; ++i) {
    // More than a pipe's capacity, before and after the blocked checkpoint.
    for (int j = 0; j < 2000; ++j) fprintf(stderr, "retained-instance-%d-before-%d\n", i, j);
    VkLayerInstanceLink ilink = {.pfnNextGetInstanceProcAddr=gipa};
    VkLayerInstanceCreateInfo ichain = {.sType=VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO,
      .function=VK_LAYER_LINK_INFO, .u.pLayerInfo=&ilink};
    VkInstanceCreateInfo ci = {.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pNext=&ichain, .flags=7};
    expected_instance_info = &ci;
    VkInstance instance;
    VkResult result = ((PFN_vkCreateInstance)interface.pfnGetInstanceProcAddr(NULL, "vkCreateInstance"))(&ci, &allocator, &instance);
    if (result != VK_SUCCESS) return 6;
    if (getenv("FAKE_HOLD_AFTER_ACK")) {
      fprintf(stderr, "ACKED-HOLD\n"); fflush(stderr); sleep(60);
    }
    VkLayerDeviceLink dlink = {.pfnNextGetInstanceProcAddr=gipa, .pfnNextGetDeviceProcAddr=gdpa};
    VkLayerDeviceCreateInfo dchain = {.sType=VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO,
      .function=VK_LAYER_LINK_INFO, .u.pLayerInfo=&dlink};
    VkDeviceCreateInfo di = {.sType=VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext=&dchain, .flags=11};
    expected_device_info = &di;
    VkDevice device;
    result = ((PFN_vkCreateDevice)interface.pfnGetInstanceProcAddr(instance, "vkCreateDevice"))((VkPhysicalDevice)instance, &di, &allocator, &device);
    if (result != VK_SUCCESS) return 7;
    ((PFN_vkDestroyDevice)interface.pfnGetDeviceProcAddr(device, "vkDestroyDevice"))(device, &allocator);
    for (int j = 0; j < 2000; ++j) fprintf(stderr, "retained-instance-%d-after-%d\n", i, j);
    ((PFN_vkDestroyInstance)interface.pfnGetInstanceProcAddr(instance, "vkDestroyInstance"))(instance, &allocator);
  }
  fail_create = 1;
  VkLayerInstanceLink link = {.pfnNextGetInstanceProcAddr=gipa};
  VkLayerInstanceCreateInfo chain = {.sType=VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO,
    .function=VK_LAYER_LINK_INFO, .u.pLayerInfo=&link};
  VkInstanceCreateInfo ci = {.sType=VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pNext=&chain, .flags=7};
  expected_instance_info = &ci;
  VkInstance instance;
  if (((PFN_vkCreateInstance)interface.pfnGetInstanceProcAddr(NULL, "vkCreateInstance"))(&ci, &allocator, &instance) != VK_ERROR_DEVICE_LOST) return 8;
  const char* errors = getenv("FAKE_CONTROL_ERRORS");
  if (errors) fputs(errors, stderr);
  puts("AUTHORED_CHILD_OK");
  return 0;
}
"""

@pytest.fixture(scope="module")
def authored(tmp_path_factory):
    base = tmp_path_factory.mktemp("validation-runtime")
    layer, child = base / "libVkLayer_khronos_validation_fake.so", base / "child"
    header = ROOT / "private/s5-tools/src/dxvk-3.1.1/include/vulkan/include"
    for name, text in (("layer.c", LAYER_C), ("child.c", CHILD_C)):
        (base / name).write_text(text)
    subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                    "-I"+str(header), str(base/"layer.c"), "-o", str(layer)], check=True)
    subprocess.run(["gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-I"+str(header),
                    str(base/"child.c"), "-ldl", "-o", str(child)], check=True)
    subprocess.run(["bash",str(ROOT/"host/d3d9gpu/test/validation_probe/build_validation_probe.sh"),str(base/"probe")],
                   env={**os.environ,"SA2_REPO":str(ROOT)},check=True,capture_output=True)
    manifest = base / "manifest.json"
    manifest.write_text(json.dumps({"layer":{"name":runtime.LAYER_NAME,"api_version":"1.4.350","library_path":str(layer)}}))
    status = layer.stat()
    selected = {"key":validation.SELECTED_KEY, "prefix":str(base), "library":str(layer), "manifest":str(manifest),
        "library_sha256":runtime.sha(layer), "manifest_sha256":runtime.sha(manifest), "api_version":"1.4.350",
        "loader_environment":{"LD_LIBRARY_PATH":str(base)},
        "expected_mapping":{"realpath":str(layer),"device":status.st_dev,"inode":status.st_ino,
                            "sha256":runtime.sha(layer),"api_version":"1.4.350"}}
    env = {key:value for key,value in os.environ.items() if not key.startswith(("VK_","SA2_VALIDATION_","FAKE_"))}
    env.update(VK_INSTANCE_LAYERS=runtime.LAYER_NAME,VK_ADD_LAYER_PATH=str(base),LD_LIBRARY_PATH=str(base),
               VK_DRIVER_FILES=runtime.ICDS["nvidia"],SDL_LOGGING="gpu=info",SDL_VIDEO_DRIVER="offscreen")
    return base, child, layer, selected, runtime.probe_identity(base/"probe",ROOT), env


def run_child(authored, tmp_path, extra=None, verify=None):
    base,child,layer,selected,probe,env=authored
    with (tmp_path/"stdout").open("wb") as output:
        return runtime.observed_command([str(child),str(layer),probe["library"]], selected=selected,
            verify=verify or validation.verify_loaded_layer, probe=probe, record_path=tmp_path/"live.json",
            environment={**env,**(extra or {})},stdout=output)


def test_each_live_instance_is_verified_and_all_stderr_is_retained(authored,tmp_path):
    record,stderr=run_child(authored,tmp_path)
    assert record["pass"] is True and record["producer_exit"]==0
    assert len(record["observations"])==2
    assert all(row["pid"]==record["pid"] and row["matched_map_lines"] for row in record["observations"])
    assert (tmp_path/"stdout").read_text()=="AUTHORED_CHILD_OK\n"
    assert len(stderr.splitlines())==8000
    assert stderr.startswith(b"retained-instance-0-before-0\n")
    assert b"retained-instance-1-after-1999\n" in stderr
    assert (tmp_path/"live.stderr").read_bytes()==stderr
    assert record["stderr_sha256"]==runtime.sha(tmp_path/"live.stderr")


@pytest.mark.parametrize("extra,want",[
    ({"FAKE_OLD_API":"1"},"api_version"),
    ({"FAKE_MISSING_LAYER":"1"},"no live instance observation"),
    ({"FAKE_NO_PROBE":"1"},"no live instance observation"),
])
def test_missing_observer_library_and_wrong_actual_api_fail(authored,tmp_path,extra,want):
    record,_=run_child(authored,tmp_path,extra)
    assert record["pass"] is False and want in record["failure"]


@pytest.mark.parametrize("field,value",[("realpath","/other.so"),("device",0),("inode",0),("sha256","0"*64)])
def test_child_identity_disagreement_fails_before_ack(authored,tmp_path,field,value):
    def changed(observed,selected):
        return validation.verify_loaded_layer({**observed,field:value},selected)
    record,_=run_child(authored,tmp_path,verify=changed)
    assert record["pass"] is False and field in record["failure"]


@pytest.mark.parametrize("selected_change,env_change,want",[
    ({"key":"vulkan-validation-layers"},{},"wrong selected"),
    ({"library_sha256":"0"*64},{},"changed before launch"),
    ({"manifest_sha256":"0"*64},{},"changed before launch"),
    ({},{"VK_ADD_LAYER_PATH":"/other"},"another validation manifest"),
    ({},{"LD_LIBRARY_PATH":"/other"},"another validation library"),
    ({},{"VK_LAYER_MESSAGE_ID_FILTER":"ignored"},"unexpected inherited"),
])
def test_selection_and_filter_disagreement_refuses_launch(authored,tmp_path,selected_change,env_change,want):
    _,child,layer,selected,probe,env=authored
    with pytest.raises(runtime.ObservationRefused,match=want):
        runtime.observed_command([str(child),str(layer),probe["library"]],selected={**selected,**selected_change},
            verify=validation.verify_loaded_layer,probe=probe,record_path=tmp_path/"live.json",
            environment={**env,**env_change})
    assert not (tmp_path/"live.json").exists()


def test_expired_child_cannot_supply_live_mapping(authored):
    selected=authored[3]
    with pytest.raises(validation.ValidationRefused,match="loaded layer proof failed"):
        validation.verify_loaded_layer(dict(selected["expected_mapping"],pid=999999999),selected)


def test_r1_reconciles_every_live_job_and_rejects_artifact_substitutions(authored,tmp_path):
    _,child,layer,selected,_,env=authored
    run=tmp_path/"r1"
    subprocess.run(["bash",str(ROOT/"host/d3d9gpu/test/validation_probe/build_validation_probe.sh"),str(run/"validation-probe")],
                   env={**os.environ,"SA2_REPO":str(ROOT)},check=True,capture_output=True)
    probe=runtime.probe_identity(run/"validation-probe",ROOT)
    for driver in ("nvidia","lvp"):
        for job,mode in runtime.R1_JOBS.items():
            output=run/driver/(job+".txt");output.parent.mkdir(parents=True,exist_ok=True)
            job_env={**env,"FAKE_LAYER_PATH":str(layer),"FAKE_PROBE_PATH":probe["library"]}
            job_env["VK_DRIVER_FILES"] = runtime.ICDS[driver]
            if job=="load":
                job_env["VK_INSTANCE_LAYERS"] += ":VK_LAYER_SA2_vkcap"
                job_env["VK_ADD_LAYER_PATH"] += ":" + str(run/"vkcap/vkcap")
                job_env["VKCAP_OUT"] = str(run/driver/"load-capture")
            if job in runtime.CONTROL_VUIDS:
                job_env["FAKE_CONTROL_ERRORS"] = "".join("Validation Error: [ " + vuid + " ] authored expected control\n"
                    for vuid in sorted(runtime.CONTROL_VUIDS[job]))
            with output.open("wb") as stdout:
                record,collected=runtime.observed_command([str(child),mode,"authored-arguments"],selected=selected,
                    verify=validation.verify_loaded_layer,probe=probe,record_path=run/driver/"validation"/(job+".json"),
                    environment=job_env,stdout=stdout)
            assert record["pass"] and record["producer_exit"]==0
            # This CPU producer is not VVL; the receipt-boundary fixture supplies the logger line.
            with output.open("ab") as stdout: stdout.write(runtime.LAYER_STARTUP.encode()+b"\n"+collected)
    audited=runtime.audit_r1(run,selected,child,ROOT)
    assert len(audited["jobs"])==14
    assert all(len(job["observations"])==2 for job in audited["jobs"].values())
    path=run/"nvidia/validation/load.json"
    original=path.read_bytes()
    original_data=json.loads(original)
    def no_record(): path.unlink()
    def change(field,value):
        data=copy.deepcopy(original_data);data[field]=value;path.write_text(json.dumps(data))
    for edit,want in [
        (no_record,"missing or extra"),
        (lambda:change("pass",False),"observation failed"),
        (lambda:change("producer_sha256","0"*64),"another producer"),
        (lambda:change("producer_exit",1),"exited unexpectedly"),
        (lambda:change("selected",dict(selected,key="vulkan-validation-layers")),"selected layer or observer"),
        (lambda:change("observations",[]),"no actual live"),
        (lambda:change("observations",[dict(original_data["observations"][0],api_version="1.4.309")]),"loaded identity differs"),
        (lambda:change("observations",[dict(original_data["observations"][0],matched_map_lines=[])]),"omitted its live"),
        (lambda:change("observations",[dict(original_data["observations"][0],matched_map_lines=["1000-2000 r-xp 0 0:0 1 /other.so"])]),"mapping does not match"),
        (lambda:change("stderr_sha256","0"*64),"collected bytes")]:
        edit()
        with pytest.raises(runtime.ObservationRefused,match=want):runtime.audit_r1(run,selected,child,ROOT)
        path.write_bytes(original)
    assert runtime.audit_r1(run,selected,child,ROOT)["jobs"]==audited["jobs"]


def test_the_actual_debug_shell_caller_accepts_the_observed_layers_and_keeps_its_guards(authored,tmp_path):
    record,_=run_child(authored,tmp_path)
    assert record["pass"] and record["producer_exit"]==0
    layers=record["environment"]["VK_INSTANCE_LAYERS"]
    assert layers=="VK_LAYER_SA2_validation_probe:VK_LAYER_KHRONOS_validation"
    source=(ROOT/"host/d3d9gpu/test_d3d9gpu.sh").read_text()
    begin=source.index('verrors=$(grep -c "Validation Error" "$R/debug.txt")')
    end=source.index('echo "== 6 lavapipe (informational)"',begin)
    caller=source[begin:end]
    expected="and d['env']['VK_INSTANCE_LAYERS'] == 'VK_LAYER_SA2_validation_probe:VK_LAYER_KHRONOS_validation' else 1)"
    old="and d['env']['VK_INSTANCE_LAYERS'] == 'VK_LAYER_KHRONOS_validation' else 1)"
    assert caller.count(expected)==1
    mutant=caller.replace(expected,old)
    good={"sdl_gpu":{"debug_mode":True},"complete":True,"not_implemented":{"count":0},
          "env":{"VK_INSTANCE_LAYERS":layers}}
    log="Validation layers enabled\n"
    def check(name,data,exit_code=0,text=log,body=caller):
        directory=tmp_path/name;directory.mkdir()
        (directory/"debug.d3d9gpu.json").write_text(json.dumps(data))
        (directory/"debug.txt").write_text(text)
        result=subprocess.run(["bash","-c",
            'fail=0; bad() { echo "$*" >&2; fail=1; }; rc=$CHILD_EXIT;\n'+body+'\nexit "$fail"'],
            env={**os.environ,"R":str(directory),"PY":sys.executable,"CHILD_EXIT":str(exit_code)},
            capture_output=True,text=True)
        return result.returncode,result.stdout,result.stderr
    assert check("correct-observed-positive",good)[0]==0
    assert check("old-predicate-mutant",good,body=mutant)[0]==1
    for name,value in [
        ("missing-probe","VK_LAYER_KHRONOS_validation"),
        ("missing-validation","VK_LAYER_SA2_validation_probe"),
        ("missing-both",""),
        ("duplicate-probe","VK_LAYER_SA2_validation_probe:VK_LAYER_SA2_validation_probe:VK_LAYER_KHRONOS_validation"),
        ("duplicate-validation","VK_LAYER_SA2_validation_probe:VK_LAYER_KHRONOS_validation:VK_LAYER_KHRONOS_validation"),
        ("reordered","VK_LAYER_KHRONOS_validation:VK_LAYER_SA2_validation_probe"),
    ]:
        data=copy.deepcopy(good);data["env"]["VK_INSTANCE_LAYERS"]=value
        assert check(name,data)[0]==1,name
    for name,field,value in [("debug-off","sdl_gpu",{"debug_mode":False}),
                             ("incomplete","complete",False),
                             ("unimplemented","not_implemented",{"count":1})]:
        data=copy.deepcopy(good);data[field]=value
        assert check(name,data)[0]==1,name
    assert check("nonzero-child-exit",good,exit_code=1)[0]==1
    assert check("validation-error",good,text=log+"Validation Error: authored negative control\n")[0]==1
    assert check("missing-layer-log",good,text="authored unrelated log\n")[0]==1
    assert check("layer-not-found",good,text=log+"Validation layers not found\n")[0]==1


@pytest.mark.parametrize("change",[
    {"schema":None},{"instance":None},{"instance":"0x0"},{"instance":"0xz"},{"instance":"0x10000000000000000"},
])
def test_parent_rejects_missing_or_invalid_live_instance_fields(authored,tmp_path,change):
    _,_,_,selected,probe,environment=authored
    producer=tmp_path/"bad-instance.py"
    producer.write_text(
        'import json,os,socket\n'
        's=socket.socket(fileno=int(os.environ["SA2_VALIDATION_OBSERVER_FD"]))\n'
        'd={"schema":"sa2-validation-live/v1","pid":os.getpid(),"instance":"0x1"}\n'
        'd.update('+repr(change)+')\n'
        's.send(json.dumps(d).encode());s.recv(2)\n')
    record,_=runtime.observed_command([sys.executable,str(producer)],selected=selected,
        verify=validation.verify_loaded_layer,probe=probe,record_path=tmp_path/"invalid-live.json",environment=environment)
    assert record["pass"] is False
    assert "malformed observer record" in record["failure"] or "concrete live instance" in record["failure"]


@pytest.mark.parametrize("cancellation", [KeyboardInterrupt, SystemExit])
def test_cancellation_reaps_acknowledged_child_and_keeps_stderr(authored,tmp_path,cancellation):
    _,child,layer,selected,probe,environment=authored
    class Cancel:
        def write(self,chunk):
            if b"ACKED-HOLD" in chunk:
                raise cancellation("authored cancellation after positive ACK")
            return len(chunk)
        def flush(self):
            pass
    def invoke(module,directory):
        directory.mkdir()
        with (directory/"stdout").open("wb") as output:
            with pytest.raises(cancellation):
                module.observed_command([str(child),str(layer),probe["library"]],selected=selected,
                    verify=validation.verify_loaded_layer,probe=probe,record_path=directory/"live.json",
                    environment={**environment,"FAKE_HOLD_AFTER_ACK":"1"},stdout=output,stderr_sink=Cancel())
        return json.loads((directory/"live.json").read_text())
    correct=tmp_path/"correct"
    record=invoke(runtime,correct)
    assert record["pass"] is False and record["producer_exit"]==-9
    assert cancellation.__name__ in record["failure"] and len(record["observations"])==1
    assert not Path(f'/proc/{record["pid"]}').exists()
    retained=(correct/"live.stderr").read_bytes()
    assert b"ACKED-HOLD\n" in retained
    assert len([line for line in retained.splitlines() if line.startswith(b"retained-instance-0-before-")])==2000
    assert record["stderr_sha256"]==runtime.sha(correct/"live.stderr") and record["stderr_bytes"]==len(retained)
    source=(ROOT/"host/d3d9gpu/test/validation_run.py").read_text()
    assert source.count("    except BaseException as error:\n")==1
    mutant_path=tmp_path/"cancellation-mutant.py"
    mutant_path.write_text(source.replace("    except BaseException as error:\n","    except Exception as error:\n"))
    mutant=load("validation_cancellation_mutant",mutant_path)
    old=invoke(mutant,tmp_path/"mutant")
    try:
        assert old["producer_exit"] is None and Path(f'/proc/{old["pid"]}/maps').exists()
        assert validation.verify_loaded_layer(old["observations"][0],selected)
    finally:
        os.kill(old["pid"],9)
        os.waitpid(old["pid"],0)


def test_ack_deadline_survives_interrupts_and_rejects_late_ack(tmp_path):
    source=(ROOT/"host/d3d9gpu/test/validation_probe/validation_probe.c").read_text()
    start=source.index("static int acknowledged(int fd) {")
    helper=source[start:source.index("static int checkpoint(VkInstance instance)",start)]
    helper=helper.replace("clock_gettime(","controlled_clock(").replace("poll(&wait,","controlled_poll(&wait,")
    harness=r'''#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
static int scenario, ticks, calls, bad_timeout;
static int controlled_clock(clockid_t clock, struct timespec* out) {
  if (clock != CLOCK_MONOTONIC || scenario == 3) return -1;
  const int64_t interrupted[] = {0,0,1000000000,2000000000,8000000000,30000000000};
  int64_t ns = scenario == 0 ? interrupted[ticks < 6 ? ticks : 5] :
               scenario == 4 && ticks == 2 ? 30000000000 : 0;
  ++ticks; out->tv_sec = (time_t)(ns / 1000000000); out->tv_nsec = (long)(ns % 1000000000);
  return 0;
}
static int controlled_poll(struct pollfd* fds, nfds_t count, int timeout) {
  const int timeouts[] = {30000,29000,28000,22000};
  if (count != 1 || timeout != (scenario == 0 && calls < 4 ? timeouts[calls] : 30000)) bad_timeout = 1;
  ++calls;
  if (scenario == 0) { errno = EINTR; return calls <= 4 ? -1 : 0; }
  fds[0].revents = POLLIN; return 1;
}
/* ACTUAL_HELPER */
int main(int argc, char** argv) {
  if (argc != 2) return 39;
  scenario=argv[1][0]-'0';
  int pair[2];
  if (socketpair(AF_UNIX, SOCK_SEQPACKET, 0, pair)) return 40;
  if (scenario == 0) {
    if (acknowledged(pair[0]) || calls != 4 || ticks != 6 || bad_timeout) return 41;
  } else {
    if (scenario != 3 && send(pair[1],scenario == 2 ? "N" : "Y",1,0) != 1) return 42;
    int accepted=acknowledged(pair[0]);
    if (accepted != (scenario == 1) || bad_timeout || (scenario == 3 && calls != 0)) return 43+scenario;
  }
  close(pair[0]); close(pair[1]); return 0;
}
'''
    def run(name,body):
        c=tmp_path/(name+".c");program=tmp_path/name
        c.write_text(harness.replace("/* ACTUAL_HELPER */",body))
        subprocess.run(["gcc","-std=c11","-O2","-Wall","-Wextra","-Werror",str(c),"-o",str(program)],check=True)
        return [subprocess.run([str(program),str(i)],check=False).returncode for i in range(5)]
    assert run("absolute-deadline",helper)==[0]*5
    assert helper.count("controlled_poll(&wait, 1, milliseconds)")==1
    mutant=helper.replace("controlled_poll(&wait, 1, milliseconds)","controlled_poll(&wait, 1, 30000)")
    mutant=mutant.replace("int milliseconds = (int)((remaining + 999999) / 1000000);",
                          "int milliseconds = (int)((remaining + 999999) / 1000000); (void)milliseconds;")
    assert run("renewed-timeout-mutant",mutant)==[41,0,0,0,0]


def test_refused_checkpoint_never_reuses_an_acknowledgement(tmp_path):
    source=(ROOT/"host/d3d9gpu/test/validation_probe/validation_probe.c").read_text()
    start=source.index("static int acknowledged(int fd) {")
    helper=source[start:source.index("static int checkpoint(VkInstance instance)",start)]
    helper=helper.replace("clock_gettime(","controlled_clock(").replace("poll(&wait,","controlled_poll(&wait,")
    harness=r'''#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
static int scenario, reads;
static int controlled_clock(clockid_t id, struct timespec* out) {
  if (id != CLOCK_MONOTONIC) return -1;
  if (scenario == 3 && reads++ == 0) return -1;
  const int64_t late[] = {0,0,30000000000LL,30000000000LL,30000000000LL,30000000000LL};
  int64_t n=(scenario == 0 || scenario == 4) ? late[reads<6 ? reads : 5] : 0;
  ++reads;out->tv_sec=n/1000000000;out->tv_nsec=n%1000000000;return 0;
}
static int controlled_poll(struct pollfd* fd,nfds_t count,int timeout) {
  if (count != 1 || timeout != 30000) return -1;
  fd->revents=POLLIN;return 1;
}
/* ACTUAL_HELPER */
int main(int argc,char** argv) {
  if (argc != 2) return 39;
  scenario=argv[1][0]-'0';
  int pair[2];if(socketpair(AF_UNIX,SOCK_SEQPACKET,0,pair))return 40;
  int ordinary=0;
  if (scenario == 5) {
    for (int i=0;i<2;++i) {
      if(send(pair[1],"Y",1,0)!=1 || acknowledged(pair[0])!=1)return 41;
      ++ordinary;
    }
  }
  if (scenario == 1 || scenario == 5) { if(send(pair[1],"N",1,0)!=1)return 42; }
  if (scenario == 2) { if(send(pair[1],"YY",2,0)!=2)return 43; }
  if(send(pair[1],"Y",1,0)!=1)return 44;
  int first=acknowledged(pair[0]);
  if (scenario == 4) {
    int fd=pair[0];close(pair[0]);close(pair[1]);
    if(socketpair(AF_UNIX,SOCK_SEQPACKET,0,pair) || pair[0]!=fd || send(pair[1],"Y",1,0)!=1)return 45;
  }
  int second=acknowledged(pair[0]);
  printf("first=%d second=%d ordinary_acknowledgements=%d scenario=%d\n",first,second,ordinary,scenario);
  close(pair[0]);close(pair[1]);return first==0 && second==0 ? 0 : 46;
}
'''
    def run(name,body):
        c=tmp_path/(name+".c");program=tmp_path/name
        c.write_text(harness.replace("/* ACTUAL_HELPER */",body))
        subprocess.run(["gcc","-std=c11","-O2","-Wall","-Wextra","-Werror",str(c),"-o",str(program)],check=True)
        results=[]
        for i in range(6):
            result=subprocess.run([str(program),str(i)],check=False,capture_output=True,text=True)
            results.append({"scenario":i,"exit":result.returncode,"output":result.stdout.strip()})
        return results
    correct=run("permanent-refusal",helper)
    assert [r["exit"] for r in correct]==[0]*6
    assert correct[0]["output"]=="first=0 second=0 ordinary_acknowledgements=0 scenario=0"
    assert correct[5]["output"]=="first=0 second=0 ordinary_acknowledgements=2 scenario=5"
    mutant=helper.replace("  static int refused;\n  if (refused) return 0;\n","")
    mutant=mutant.replace("  refused = 1;\n  shutdown(fd, SHUT_RDWR);\n","")
    assert mutant != helper
    old=run("stale-ack-mutant",mutant)
    assert [r["exit"] for r in old]==[46]*6
    assert old[0]["output"]=="first=0 second=1 ordinary_acknowledgements=0 scenario=0"
    (tmp_path/"stale-ack-fold-proof.json").write_text(json.dumps({
        "scope":"compiled actual ACK helper, controlled monotonic clock/poll, actual SOCK_SEQPACKET; no GPU",
        "probe_source_sha256":runtime.sha(ROOT/"host/d3d9gpu/test/validation_probe/validation_probe.c"),
        "correct":correct,"old_mutant":old,"pass":True},indent=2)+"\n")
