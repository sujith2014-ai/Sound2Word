set -e
export LANG=C.UTF-8 LC_ALL=C.UTF-8
apt-get update -qq && apt-get install -y -qq build-essential python3 python3-venv git socat >/dev/null
mkdir /work && cd /src && tar --exclude=./data --exclude=./.git --exclude=./Test -cf - . | (cd /work && tar xf -)
cd /work
find . -type f \( -name '*.py' -o -name '*.c' -o -name '*.h' -o -name Makefile -o -name '*.js' \) -print0 | xargs -0 sed -i 's/\r$//'
make all >/dev/null
python3 -m venv /opt/v && /opt/v/bin/pip install -q mlphon "setuptools<81"
socat TCP-LISTEN:8766,fork,bind=0.0.0.0 TCP:127.0.0.1:8765 &
exec /opt/v/bin/python tools/s2w_recorder.py --record-dir /voice --dictionary /dict/lexicon.csv --reference-dir /ref --unit-audio-dir /units /units_lfm /units_harish
