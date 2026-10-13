#version 450
// SDL_gpu's Vulkan binding model: fragment uniform buffers are set 3.
layout(set = 3, binding = 0) uniform Solid { vec4 color; vec4 depth; } u;
layout(location = 0) out vec4 o_color;
void main() {
    o_color = u.color;
}
