FROM python@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e
COPY build-requirements.lock runtime-requirements.lock /opt/accessforge/
RUN python -I -m pip install --no-cache-dir --no-deps --require-hashes --only-binary=:all: \
    --index-url=https://pypi.org/simple -r /opt/accessforge/build-requirements.lock \
    -r /opt/accessforge/runtime-requirements.lock
COPY build_reference.py /opt/accessforge/build_reference.py
ENV PYTHONDONTWRITEBYTECODE=1
