$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$Ffmpeg = & $Python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())'
$Background = Join-Path $ProjectRoot 'themes\first-encounter\assets\first-encounter-background-master.png'
$Lilith = Join-Path $ProjectRoot 'themes\first-encounter\assets\lilith-cracked-cutout-master.png'
$Output = Join-Path $ProjectRoot 'themes\first-encounter\assets\first-encounter-loop.mp4'
& $Ffmpeg -y -loop 1 -framerate 60 -i $Background -loop 1 -framerate 60 -i $Lilith `
    -filter_complex "[0:v]scale=2560:1707:flags=lanczos,crop=2560:1600:0:53,eq=brightness='0.010*sin(2*PI*t/6)'[bg];[1:v]scale=-1:1180:flags=lanczos,format=rgba,colorchannelmixer=aa=0.96[ch];[bg][ch]overlay=x=1710+5*sin(2*PI*t/6):y=270+4*sin(2*PI*t/4):eval=frame,format=yuv420p[out]" `
    -map '[out]' -t 12 -r 60 -an -c:v libx264 -preset medium -crf 20 -movflags +faststart $Output
