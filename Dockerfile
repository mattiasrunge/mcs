FROM ubuntu:24.04

ENV TZ=Europe/Stockholm
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

ENV DLIB_VERSION v19.17
ENV MOZJPEG_VERSION v3.3.1
ENV NODE_VERSION 10.24.1

# Create app directory
WORKDIR /usr/src/app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    software-properties-common \
    build-essential \
    wget \
    nasm \
    cmake \
    dcraw \
    git \
    pkg-config \
    libpng-dev \
    dh-autoreconf \
    libimage-exiftool-perl \
    unoconv \
    ffmpeg \
    libavformat-dev \
    libopenblas-dev \
    libx11-dev \
    imagemagick \
    libheif-plugin-libde265 \
    nano \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 10 from official binary (face-recognition 0.9.4 only builds against Node 10's V8 API)
RUN wget -q https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
    && tar -xJf node-v${NODE_VERSION}-linux-x64.tar.xz -C /usr/local --strip-components=1 \
    && rm node-v${NODE_VERSION}-linux-x64.tar.xz

# Patch bundled gyp for Python 3.12 compatibility (removes the 'U' open-mode flag)
RUN find /usr/local/lib/node_modules/npm/node_modules/node-gyp/gyp -name '*.py' \
    -exec sed -i "s/'rU'/'r'/g" {} +

# Build dlib
RUN git clone --branch $DLIB_VERSION --depth 1 https://github.com/davisking/dlib.git \
    && cd dlib \
    && mkdir build \
    && cd build \
    && cmake .. -DDLIB_NO_GUI_SUPPORT=1 -DBUILD_SHARED_LIBS=1 \
    && cmake --build .

# Install app dependencies
COPY package*.json ./
RUN DLIB_INCLUDE_DIR=/usr/src/app/dlib \
    DLIB_LIB_DIR=/usr/src/app/dlib/build/dlib \
    npm install

COPY conf/policy.xml /etc/ImageMagick-6/

# Bundle app source
COPY . .

EXPOSE 8181
CMD [ "node", "index.js" ]
