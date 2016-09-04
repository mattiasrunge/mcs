"use strict";

const path = require("path");
const fs = require("fs");
const checksum = require("checksum");
const configuration = require("./configuration");

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
            fs.mkdtemp(path.join(configuration.tempdir, "mcs-"), (error, folder) => {
                if (error) {
                    return reject(error);
                }

                resolve(folder);
            });
        });
    },
    parseExifDate: function(string) {
        let timezone = false;

        if (string[string.length - 1] === "Z") {
            timezone = "+00:00";
            string = string.substr(0, string.length - 1); // Remove trailing Z
        }

        // Replace dividing : with -
        let parts = string.split(" ");
        let date = parts[0].split(":");
        let time = parts[1].split(":");

        return {
            year: date[0],
            month: date[1],
            day: date[2],
            hour: time[0],
            minute: time[1],
            second: time[2],
            timezone: timezone
        };
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
