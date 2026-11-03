#!/bin/sh
# Records the README demo from the real crop window on a virtual screen (Xvfb), driven by xdotool:
#   docs/images/demo.gif     the animation
#   docs/images/window.jpg   a still of the window
#   img/result/*             the demo crops, written by the tool itself
#
# From the repository root (uses the GUI test image):
#   docker build -f tests/gui/Dockerfile -t batch-crop-gui .
#   docker run --rm -v "$PWD:/repo" batch-crop-gui sh /repo/docs/demo/record.sh
set -eu

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg >/dev/null

XSCREEN=:42
Xvfb "$XSCREEN" -screen 0 1280x800x24 -nolisten tcp &
export DISPLAY="$XSCREEN"
sleep 1

work=$(mktemp -d)
cd "$work"
cp -r /repo/img/input input
batch-crop -i input -o result --mismatch stretch --save-preset box.json >log.txt 2>&1 &
crop=$!
window=$(xdotool search --sync --onlyvisible --name '^batch-crop$' | head -1)
eval "$(xdotool getwindowgeometry --shell "$window")"  # sets WIDTH, HEIGHT (and SCREEN, X, Y)
size="${WIDTH}x${HEIGHT}"

ffmpeg -loglevel error -f x11grab -framerate 25 -video_size "$size" -i "$XSCREEN.0+0,0" \
  -c:v libx264rgb -crf 0 -preset ultrafast raw.mkv &
recorder=$!
sleep 1.5

# glide X0 Y0 X1 Y1 STEPS: move the mouse in small steps so the recording shows the motion
glide() {
  i=0
  while [ "$i" -le "$5" ]; do
    xdotool mousemove --window "$window" $(($1 + ($3 - $1) * i / $5)) $(($2 + ($4 - $2) * i / $5))
    sleep 0.03
    i=$((i + 1))
  done
}
press() { xdotool key --window "$window" "$@"; }

glide 150 100 300 170 10
xdotool mousedown 1; glide 300 170 760 450 30; xdotool mouseup 1   # draw
sleep 0.8
glide 760 450 761 451 2
xdotool mousedown 1; glide 761 451 800 480 15; xdotool mouseup 1   # resize by the corner
sleep 0.6
glide 800 480 560 320 12
xdotool mousedown 1; glide 560 320 520 290 15; xdotool mouseup 1  # move
sleep 0.6
for _ in 1 2 3 4; do press r; sleep 0.7; done                      # ratio 1:1, 4:3, 3:2, 16:9
press Right Right Right Up Up                                      # nudge
sleep 0.5
ffmpeg -loglevel error -y -f x11grab -video_size "$size" -i "$XSCREEN.0+0,0" -frames:v 1 window.png
press plus plus; sleep 0.9; press 0; sleep 0.6                     # zoom in and back
press h; sleep 2.6; press h; sleep 0.8                            # key help
press Return                                                       # crop everything
sleep 0.6
kill -INT "$recorder"; wait "$recorder" || true
wait "$crop"
cat log.txt

ffmpeg -loglevel error -y -i raw.mkv -vf \
  "fps=12,scale=760:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle" \
  /repo/docs/images/demo.gif
ffmpeg -loglevel error -y -i window.png -q:v 3 /repo/docs/images/window.jpg
rm -rf /repo/img/result && cp -r result /repo/img/result
ls -l /repo/docs/images/demo.gif /repo/docs/images/window.jpg /repo/img/result
