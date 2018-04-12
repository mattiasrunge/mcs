#!/usr/bin/env node

"use strict";

const fr = require("face-recognition");

const percision = 5;
const scaling = 1.5;

const detector = fr.FaceDetector(); // eslint-disable-line
const image = fr.loadImage(process.argv[2]);
const data = detector.locateFaces(image);
const faces = data.map((face) => {
    const width = face.rect.right - face.rect.left;
    const height = face.rect.bottom - face.rect.top;

    return {
        x: parseFloat(((face.rect.left + (width / 2)) / image.cols).toFixed(percision)),
        y: parseFloat(((face.rect.top + (height / 2)) / image.rows).toFixed(percision)),
        w: parseFloat(((width * scaling) / image.cols).toFixed(percision)),
        h: parseFloat(((height * scaling) / image.rows).toFixed(percision)),
        confidence: face.confidence,
        id: face.rect.area
    };
});

console.log(JSON.stringify(faces, null, 2));
