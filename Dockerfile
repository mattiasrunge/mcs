FROM ubuntu:16.04

ENV DLIB_VERSION v19.15
ENV MOZJPEG_VERSION v3.3.1

# Create app directory
WORKDIR /usr/src/app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    software-properties-common \
    wget \
    nasm \
    cmake \
    pkg-config \
    libpng-dev \
    dh-autoreconf \
    ufraw-batch \
    libimage-exiftool-perl \
    unoconv

# Install nodejs
RUN wget -qO- https://deb.nodesource.com/setup_10.x | bash -

# Install system dependencies
RUN add-apt-repository ppa:jonathonf/ffmpeg-4 \
    && apt-get update && apt-get install -y \
    ffmpeg \
    libavformat-dev \
    libopenblas-dev \
    nodejs \
    git \
    libx11-dev \
    imagemagick \
    && rm -rf /var/lib/apt/lists/*

# Build mozjpeg
RUN wget https://github.com/mozilla/mozjpeg/archive/$MOZJPEG_VERSION.tar.gz \
    && tar -xvf $MOZJPEG_VERSION.tar.gz \
    && cd mozjpeg* \
    && autoreconf -fiv \
    && mkdir build && cd build \
    && sh ../configure \
    && make install \
    && ln -s /opt/mozjpeg/bin/cjpeg /usr/local/bin/mozjpeg

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

# Bundle app source
COPY . .

EXPOSE 8181
CMD [ "node", "index.js" ]
