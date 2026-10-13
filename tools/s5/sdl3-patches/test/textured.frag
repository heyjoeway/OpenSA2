#version 450
// SDL_gpu's Vulkan binding model: fragment sampled textures are set 2.
layout(set = 2, binding = 0) uniform sampler2D u_texture;
layout(location = 0) in vec2 v_uv;
layout(location = 0) out vec4 o_color;
void main() {
    o_color = texture(u_texture, v_uv);
}
