"use strict";

const fs = require("fs");

module.exports = {
    createDeferred: () => {
        let reject;
        let resolve;
        let promise = new Promise((a, b) => {
            resolve = a;
            reject = b;
        });

        return {
            reject: reject,
            resolve: resolve,
            promise: promise
        };
    },
    createTmpDir: () => {
        return new Promise((resolve, reject) => {
            fs.mkdtemp("/tmp/mcs-", (error, folder) => {
                if (error) {
                    return reject(error);
                }

                resolve(folder);
            });
        });
    }
};
