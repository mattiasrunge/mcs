"use strict";

const fs = require("fs");
const checksum = require("checksum");

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
    },
    cleanExifDate: function(string) {
        if (string[string.length - 1] === "Z") {
            string = string.substr(0, string.length - 1); // Remove trailing Z
        }

        // Replace dividing : with -
        let parts = string.split(" ");
        return parts[0].replace(/:/g, "-") + " " + parts[1];
    },
    checksumFile: (filename, algorithm) => {
        return new Promise((resolve, reject) => {
            let options = {};

            if (algorithm) {
                options.algorithm = algorithm;
            }

            checksum.file(filename, options, (error, sum) => {
                if (error) {
                    return reject(error);
                }

                resolve(sum);
            });
        });
    }
};
