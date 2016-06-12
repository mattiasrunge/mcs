"use strict";

const co = require("bluebird").coroutine;
const request = require("request-promise");
const api = require("api.io");
const moment = require("moment-timezone");

let params = {};

/* Example:
    let sources = {
        manual: {
            year: "2016",
            month: "06",
            day: "10",
            hour: "22",
            minute: "01",
            second: "55",
            timezone: "+01:00"
        },
        gps: {
            year: "2016",
            month: "06",
            day: "10",
            hour: "22",
            minute: "01",
            second: "55",
            timezone: "+00:00"
        },
        device: {
            year: "2016",
            month: "06",
            day: "10",
            hour: "22",
            minute: "01",
            second: "55",
            deviceAutoDst: true,

            deviceType: "offset_fixed",
            deviceUtcOffset: 3456

            deviceType: "offset_relative_to_position",
            longitude: 0,
            latitude: 0,
        }
    }
*/

let time = api.register("time", {
    init: co(function*(config) {
        params = config;
    }),
    compile: function*(session, sources) {
        if (!session.authenticated) {
            throw new Error("Not authenticated");
        }

        let source = false;
        let sourceIsDstAdjusted = false;
        let sourceType = false;

        if (sources.manual) {
            source = sources.manual;
            sourceType = "manual";
            sourceIsDstAdjusted = true;
        } else if (sources.gps) {
            source = sources.gps;
            sourceType = "gps";
            sourceIsDstAdjusted = false;
        } else if (sources.device) {
            source = sources.device;
            sourceType = "device";
            sourceIsDstAdjusted = source.deviceAutoDst;
        }

        let compiled = module.exports.parse(source, sourceIsDstAdjusted);

        if (compiled.quality !== false && sourceType === "device") {
            if (source.deviceType === "offset_relative_to_position") {
                if (source.longitude && source.latitude) {
                    let utcOffset = yield module.exports.utcOffsetByPosition(source.longitude, source.latitude);

                    compiled.timestamp -= utcOffset;
                    compiled.quality = "utc";
                }
            } else if (source.deviceType === "offset_fixed") {
                compiled.timestamp -= source.deviceUtcOffset;
                compiled.quality = "utc";
            }
        }

        compiled.source = sourceType;

        return compiled;
    },
    utcOffsetByPosition: co(function*(longitude, latitude) {
        let options = {
            uri: "https://maps.googleapis.com/maps/api/timezone/json",
            qs: {
                location: latitude + "," + longitude,
                timestamp: 0,
                key: params.googleKey
            },
            json: true
        };

        let data = yield request(options);
        /*{
            "dstOffset" : 3600,
            "rawOffset" : 0,
            "status" : "OK",
            "timeZoneId" : "America/New_York",
            "timeZoneName" : "Eastern Standard Time"
        }*/

        if (data.status !== "OK") {
            throw new Error(data.errorMessage);
        }

        return data.rawOffset;
    }),
    parse: (source, sourceIsDstAdjusted) => {
        let timestamp = false;
        let quality = false;

        if (source) {
            let utcOffset = 0;
            let dstOffset = 0;
            let foundRange = false;
            let spec = {};
            quality = "fuzzy";

            if (source.year) {
                foundRange = source.year instanceof Array;
                spec.year = parseInt(foundRange ? source.year[0] : source.year, 10);
            }

            if (source.month && !foundRange) {
                foundRange = source.month instanceof Array;
                spec.month = parseInt(foundRange ? source.month[0] : source.month, 10) - 1; // Moment expects zero based
            }

            if (source.day && !foundRange) {
                foundRange = source.day instanceof Array;
                spec.day = parseInt(foundRange ? source.day[0] : source.day, 10);
            }

            if (source.hour && !foundRange) {
                foundRange = source.hour instanceof Array;
                spec.hour = parseInt(foundRange ? source.hour[0] : source.hour, 10);
            }

            if (source.minute && !foundRange) {
                foundRange = source.minute instanceof Array;
                spec.minute = parseInt(foundRange ? source.minute[0] : source.minute, 10);
            }

            if (source.second && !foundRange) {
                foundRange = source.second instanceof Array;
                spec.second = parseInt(foundRange ? source.second[0] : source.second, 10);

                if (!foundRange) {
                    quality = "local";

                    if (sourceIsDstAdjusted) {
                        dstOffset = moment(spec).isDST() ? 3600 : 0;
                    }
                }
            }

            if (source.timezone && !foundRange) {
                utcOffset = moment(spec).utcOffset(source.timezone).utcOffset() * 60;
                quality = "utc";
            }

            timestamp = moment.utc(spec).unix() - utcOffset - dstOffset;
        }

        return {
            timestamp: timestamp,
            quality: quality
        };
    }
});

module.exports = time;
