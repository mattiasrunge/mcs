"use strict";

module.exports = {
    createDeferred: function() {
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
    }
};
