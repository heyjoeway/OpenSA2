#version 450
// As solid.frag, with the fragment's depth written by the shader (u.depth.x), which depth clamp
// also clamps to the viewport's range.
layout(set = 3, binding = 0) uniform Solid { vec4 color; vec4 depth; } u;
layout(location = 0) out vec4 o_color;
void main() {
    o_color = u.color;
    gl_FragDepth = u.depth.x;
}
