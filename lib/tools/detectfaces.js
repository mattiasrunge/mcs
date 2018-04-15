#!/usr/bin/env node

"use strict";

const path = require("path");
const uuid = require("uuid");
const fr = require("face-recognition");
const { name, version } = require(path.join(__dirname, "..", "..", "node_modules", "face-recognition", "package.json"));

const percision = 5;
const scaling = 1.5;

const round = (value) => parseFloat(value.toFixed(percision));
const detector = fr.FaceDetector(); // eslint-disable-line
const image = fr.loadImage(process.argv[2]);
const data = detector.locateFaces(image);
const faces = data.map((face) => {
    const width = face.rect.right - face.rect.left;
    const height = face.rect.bottom - face.rect.top;

    const x = round((face.rect.left + (width / 2)) / image.cols);
    const y = round((face.rect.top + (height / 2)) / image.rows);
    const w = round((width * scaling) / image.cols);
    const h = round((height * scaling) / image.rows);
    const id = uuid.v4();

    return {
        id,
        x,
        y,
        w,
        h,
        confidence: face.confidence,
        detector: `${name}@${version}`
    };
});

faces.sort((a, b) => a.x - b.x);

console.log(JSON.stringify(faces, null, 2));
