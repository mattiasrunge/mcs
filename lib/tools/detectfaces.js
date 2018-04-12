#!/usr/bin/env node

"use strict";

const fr = require("face-recognition");

const detector = fr.FaceDetector(); // eslint-disable-line
const image = fr.loadImage(process.argv[2]);
const data = detector.locateFaces(image);
const faces = data.map((face) => {
    const width = face.rect.right - face.rect.left;
    const height = face.rect.bottom - face.rect.top;

    return {
        x: (face.rect.left + (width / 2)) / image.cols,
        y: (face.rect.top + (height / 2)) / image.rows,
        w: (width * 1.5) / image.cols,
        h: (height * 1.5) / image.rows,
        confidence: face.confidence,
        id: face.rect.area
    };
});

console.log(JSON.stringify(faces, null, 2));
