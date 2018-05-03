FROM node:10-alpine

ENV DLIB_VERSION v19.10
ENV UFRAW_URL "http://sourceforge.net/project/downloading.php?group_id=127649&filename=ufraw-0.22.tar.gz"
ENV UNO_URL https://raw.githubusercontent.com/dagwieers/unoconv/master/unoconv

# Create app directory
WORKDIR /usr/src/app

# Install system dependencies
RUN apk add --no-cache \
    cmake \
    git \
    make \
    g++ \
    exiftool \
    ffmpeg \
    openblas-dev \
    imagemagick \
    file \
    python \
    libpng-dev \
    libx11-dev \
    glib-dev \
    lcms2-dev \
    curl \
    util-linux \
    libreoffice-common \
    libreoffice-writer \
    ttf-droid-nonlatin \
    ttf-droid \
    ttf-dejavu \
    ttf-freefont \
    ttf-liberation

# Install dlib
RUN git clone --branch $DLIB_VERSION --depth 1 https://github.com/davisking/dlib.git \
    && cd dlib \
    && mkdir build \
    && cd build \
    && cmake .. -DDLIB_NO_GUI_SUPPORT=1 -DBUILD_SHARED_LIBS=1 \
    && cmake --build .

# Install unoconv
RUN curl -Ls $UNO_URL -o /usr/local/bin/unoconv \
    && chmod +x /usr/local/bin/unoconv \
    && unlink /usr/bin/python \
    && ln -s /usr/bin/python3 /usr/bin/python

# Install ufraw
RUN curl -Ls $UFRAW_URL -o ufraw.tgz \
    && tar xzvf ufraw.tgz \
    && cd ufraw* \
    && ./configure \
    && make \
    && make install \
    && rm -rf ufraw*

# Install app dependencies
COPY package*.json ./
RUN DLIB_INCLUDE_DIR=/usr/src/app/dlib \
    DLIB_LIB_DIR=/usr/src/app/dlib/build/dlib \
    npm install

# Cleanup
RUN apk del curl make g++ cmake git \
    && rm -rf /var/cache/apk/*

# Bundle app source
COPY . .

EXPOSE 8181
CMD [ "node", "index.js" ]
