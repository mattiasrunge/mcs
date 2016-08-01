"use strict";

const co = require("bluebird").coroutine;
const api = require("api.io");
const cv = require("opencv");

let params = {};

let face = api.register("face", {
    init: co(function*(config) {
        params = config;
    }),
    detect: function*(session, filename) {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        let list = yield new Promise((resolve, reject) => {
            cv.readImage(filename, (error, im) => {
                if (error) {
                    return reject(error);
                }

                im.detectObject(cv.FACE_CASCADE, {}, (error, faces) => {
                    if (error) {
                        return reject(error);
                    }

                    faces = faces.map((face) => {
                        let x = face.x + face.width/2;
                        let y = face.y + face.height/2;
                        let w = face.width * 1.5;
                        let h = face.height * 1.5;

                        return { x: x, y: y, w: w, h: h };
                    });

                    /*
                    for (let face of faces) {
                        let x = face.x;
                        let y = face.y;
                        let w = face.w;
                        let h = face.h;

                        let top = y - h/2;
                        let left = x - w/2;
                        let bottom = y + h/2;
                        let right = x + w/2;

                        im.line([ left,top ], [ right,top ]);
                        im.line([ right,top ], [ right,bottom ]);
                        im.line([ right,bottom ], [ left,bottom ]);
                        im.line([ left,bottom ], [ left,top ]);
                    }

                    im.save('/tmp/face-detection.jpg');
                    */

                    resolve(faces);
                });
            });
        });

        return list;
    },
});

module.exports = face;
