// d9g_props.cpp: see d9g_props.h.
#include "d9g_props.h"

#include <SDL3/SDL_error.h>
#include <SDL3/SDL_properties.h>

namespace d9g {

namespace {
SDL_PropertiesID make_set(std::string* error) {
    const SDL_PropertiesID p = SDL_CreateProperties();
    if (!p) *error = std::string("SDL_CreateProperties: ") + SDL_GetError();
    return p;
}
}  // namespace

CreateProps::CreateProps(const PipelineDesc& d) {
    if (!d.depth_clamp) return;
    id_ = make_set(&error_);
    if (id_ && !SDL_SetBooleanProperty(id_, kPropDepthClamp, true))
        error_ = std::string("SDL_SetBooleanProperty(") + kPropDepthClamp + "): " + SDL_GetError();
}

CreateProps::CreateProps(const SamplerDesc& d) {
    if (d.border_colour == kBorderNone) return;
    id_ = make_set(&error_);
    if (id_ && !SDL_SetNumberProperty(id_, kPropSamplerBorderColour, d.border_colour))
        error_ = std::string("SDL_SetNumberProperty(") + kPropSamplerBorderColour + "): " + SDL_GetError();
}

CreateProps::~CreateProps() {
    if (id_) SDL_DestroyProperties(id_);
}

}  // namespace d9g
