"use strict";

const api = require("api.io");
const file = require("../file");

const face = api.register("face", {
    init: async (/* config */) => {
    },
    detect: api.export(async (session, filename) => {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        return await file.detectFaces(filename);
    })
});

module.exports = face;
