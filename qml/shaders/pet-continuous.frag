#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float phase;
    float motionGain;
    vec2 dimensions;
    vec2 direction;
    float mirrorAmount;
};
layout(binding = 1) uniform sampler2D source;
void main() {
    fragColor = texture(source, qt_TexCoord0) * qt_Opacity;
}
