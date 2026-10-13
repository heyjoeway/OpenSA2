// d9g_props.h: the SDL properties a description's create info carries, patches/sdl3's two:
// the depth-clamp boolean (PipelineDesc::depth_clamp, d9g_pipeline.h kPropDepthClamp) and the
// border colour number (SamplerDesc::border_colour, d9g_sampler.h kPropSamplerBorderColour).
//
// d9g makes them here and nowhere else, through SDL's own property functions, for both of the
// set's users: R3's device, which passes the set to SDL_CreateGPUGraphicsPipeline and
// SDL_CreateGPUSampler, and the state check, whose SDL side models those functions
// (test/ref_sdl.c). The set the check reads is therefore the one the device would pass, and a
// property dropped here is one the check sees dropped (the I6 device review's finding 1).
//
// Kept out of the CPU model's unit test (it links no SDL); the library and the state check
// link it (build.sh).
#pragma once

#include "d9g_pipeline.h"
#include "d9g_sampler.h"

#include <string>

namespace d9g {

class CreateProps {
public:
    explicit CreateProps(const PipelineDesc& d);   // kPropDepthClamp true where d.depth_clamp
    explicit CreateProps(const SamplerDesc& d);    // kPropSamplerBorderColour where d.border_colour is one
    ~CreateProps();
    CreateProps(const CreateProps&) = delete;
    CreateProps& operator=(const CreateProps&) = delete;

    // The create info's props: 0 where the description sets none (SDL's empty set), else a set
    // that lives as long as this object.
    SDL_PropertiesID id() const { return id_; }
    // "" where the set holds what the description sets; else what SDL refused, and the create
    // call must not be made with id().
    const std::string& error() const { return error_; }

private:
    SDL_PropertiesID id_ = 0;
    std::string error_;
};

}  // namespace d9g
