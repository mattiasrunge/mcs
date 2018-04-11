"use strict";

const api = require("api.io");
const fr = require("face-recognition");

const detector = fr.AsyncFaceDetector(); // eslint-disable-line

const face = api.register("face", {
    init: async (/* config */) => {
    },
    detect: api.export(async (session, filename) => {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        const image = fr.loadImage(filename);
        const faces = await detector.locateFaces(image);

        return faces.map((face) => {
            const width = face.rect.right - face.rect.left;
            const height = face.rect.bottom - face.rect.top;

            return {
                x: Math.floor(face.rect.left + (width / 2)),
                y: Math.floor(face.rect.top + (height / 2)),
                w: Math.ceil(width * 1.5),
                h: Math.ceil(height * 1.5),
                confidence: face.confidence
            };
        });
    })
});

module.exports = face;
