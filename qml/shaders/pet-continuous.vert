#version 440
layout(location = 0) in vec4 qt_Vertex;
layout(location = 1) in vec2 qt_MultiTexCoord0;
layout(location = 0) out vec2 qt_TexCoord0;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float phase;
    float motionGain;
    vec2 dimensions;
    vec2 direction;
    float mirrorAmount;
};
void main() {
    vec2 uv = qt_MultiTexCoord0;
    qt_TexCoord0 = vec2(mix(uv.x, 1.0 - uv.x, mirrorAmount), uv.y);
    // Smooth overlapping influence functions, never hard horizontal cuts.
    float head = 1.0 - smoothstep(0.25, 0.48, uv.y);
    float chestDistance = (uv.y - 0.56) / 0.15;
    float hemDistance = (uv.y - 0.75) / 0.14;
    float chest = exp(-chestDistance * chestDistance);
    float hem = exp(-hemDistance * hemDistance);
    float pinFeet = 1.0 - smoothstep(0.87, 0.98, uv.y);
    vec2 shift;
    shift.x = (uv.x - 0.5) * chest * sin(phase + 2.15) * 0.006
            + hem * sin(phase + 4.2) * 0.0015
            + head * (sin(phase + 0.15) * 0.0012 + direction.x * 0.006);
    shift.y = -chest * sin(phase + 2.15) * 0.0018
            + head * (sin(phase + 0.55) * 0.0012 + direction.y * 0.003);
    vec4 p = vec4(qt_Vertex.xy, 0.0, 1.0);
    p.xy += shift * dimensions * motionGain * pinFeet;
    gl_Position = qt_Matrix * p;
}
