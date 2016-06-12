"use strict";

const getPort = require("get-port");
const chai = require("chai");
const moment = require("moment");
const api = require("api.io").client;
const main = require("../lib/main");
const configuration = require("../lib/configuration");

chai.use(require("chai-datetime"));
require("../lib/test");
const assert = chai.assert;

describe("Time", function() {
    this.timeout(20000);

    before(function*() {
        let args = {
            level: "debug",
            config: "test/data/config.json",
            port: yield getPort(),
            keys: [ "secret key" ]
        };

        yield main.start(args);
    });

    after(function*() {
        yield main.stop();
    });

    describe("Setup", () => {
        it("should connect", function*() {
            yield api.connect({
                hostname: "localhost",
                port: configuration.port
            });
        });

        it("should authenticate", function*() {
            let result = yield api.auth.identify("secret key");

            assert.ok(result);
        });
    });

    describe("Manual", () => {
        it("should compile manual time for a year source", function*() {
            let sources = {
                manual: {
                    year: "2016"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2016-01-01T00:00:00Z");
        });

        it("should compile manual time for a year and month source", function*() {
            let sources = {
                manual: {
                    year: "2015",
                    month: "06"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2015-06-01T00:00:00Z");
        });

        it("should compile manual time for a year, month and day source", function*() {
            let sources = {
                manual: {
                    year: "2014",
                    month: "12",
                    day: "31"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2014-12-31T00:00:00Z");
        });

        it("should compile manual time for a year, month, day and hour source", function*() {
            let sources = {
                manual: {
                    year: "2013",
                    month: "01",
                    day: "01",
                    hour: "12"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2013-01-01T12:00:00Z");
        });

        it("should compile manual time for a year, month, day, hour and minute source", function*() {
            let sources = {
                manual: {
                    year: "2012",
                    month: "02",
                    day: "28",
                    hour: "23",
                    minute: "49"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2012-02-28T23:49:00Z");
        });

        it("should compile manual time for a year, month, day, hour, minute and second source", function*() {
            let sources = {
                manual: {
                    year: "2011",
                    month: "11",
                    day: "03",
                    hour: "00",
                    minute: "00",
                    second: "37"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "local");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2011-11-03T00:00:37Z");
        });

        it("should compile manual time for a year, month, day, hour, minute and second in dst source", function*() {
            let sources = {
                manual: {
                    year: "2011",
                    month: "06",
                    day: "03",
                    hour: "01",
                    minute: "00",
                    second: "37"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "local");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2011-06-03T00:00:37Z");
        });

        it("should compile manual time for a year, month, day, hour, minute, second and timezone without dst source", function*() {
            let sources = {
                manual: {
                    year: "2010",
                    month: "01",
                    day: "02",
                    hour: "03",
                    minute: "04",
                    second: "05",
                    timezone: "+01:00"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "utc");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2010-01-02T02:04:05Z");
        });

        it("should compile manual time for a year, month, day, hour, minute, second and timezone with dst source", function*() {
            let sources = {
                manual: {
                    year: "2009",
                    month: "06",
                    day: "02",
                    hour: "03",
                    minute: "04",
                    second: "05",
                    timezone: "+01:00"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "utc");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2009-06-02T01:04:05Z");
        });

        it("should compile manual time for a year, month, day, hour, minute, second and timezone #2 with dst source", function*() {
            let sources = {
                manual: {
                    year: "2008",
                    month: "06",
                    day: "02",
                    hour: "03",
                    minute: "04",
                    second: "05",
                    timezone: "+02:30"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "utc");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2008-06-01T23:34:05Z");
        });

        it("should compile manual time for a year, month, day, hour, minute, second and timezone #3 with dst source", function*() {
            let sources = {
                manual: {
                    year: "2007",
                    month: "06",
                    day: "02",
                    hour: "03",
                    minute: "04",
                    second: "05",
                    timezone: "-01:00"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "utc");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2007-06-02T03:04:05Z");
        });

        it("should compile manual time for a year, month, day, hour, minute, second and timezone #3 without dst source", function*() {
            let sources = {
                manual: {
                    year: "2007",
                    month: "01",
                    day: "02",
                    hour: "03",
                    minute: "04",
                    second: "05",
                    timezone: "-01:00"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "utc");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2007-01-02T04:04:05Z");
        });
    });

    describe("Manual with ranges", () => {
        it("should compile manual time for a year source", function*() {
            let sources = {
                manual: {
                    year: [ "2016", "2017" ]
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2016-01-01T00:00:00Z");
        });

        it("should compile manual time for a year and month source", function*() {
            let sources = {
                manual: {
                    year: [ "2015", "2016" ],
                    month: [ "06", "07" ]
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2015-01-01T00:00:00Z");
        });

        it("should compile manual time for a year, month and day source", function*() {
            let sources = {
                manual: {
                    year: "2014",
                    month: [ "11", "12" ],
                    day: [ "28", "31" ]
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2014-11-01T00:00:00Z");
        });

        it("should compile manual time for a year, month, day and hour source", function*() {
            let sources = {
                manual: {
                    year: [ "2013", "2014" ],
                    month: [ "01", "06" ],
                    day: [ "01", "30" ],
                    hour: [ "12", "23" ]
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2013-01-01T00:00:00Z");
        });

        it("should compile manual time for a year, month, day, hour and minute source", function*() {
            let sources = {
                manual: {
                    year: [ "2012", "2017" ],
                    month: [ "02", "12" ],
                    day: "28",
                    hour: "23",
                    minute: [ "49", "53" ]
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2012-01-01T00:00:00Z");
        });

        it("should compile manual time for a year, month, day, hour, minute and second source", function*() {
            let sources = {
                manual: {
                    year: "2011",
                    month: "11",
                    day: [ "03", "06" ],
                    hour: "00",
                    minute: "00",
                    second: "37"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2011-11-03T00:00:00Z");
        });

        it("should compile manual time for a year, month, day, hour, minute and second in dst source", function*() {
            let sources = {
                manual: {
                    year: "2011",
                    month: "06",
                    day: [ "03", "08" ],
                    hour: "01",
                    minute: "00",
                    second: "37"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2011-06-03T00:00:00Z");
        });

        it("should compile manual time for a year, month, day, hour, minute, second and timezone without dst source", function*() {
            let sources = {
                manual: {
                    year: "2010",
                    month: [ "01", "07" ],
                    day: "02",
                    hour: "03",
                    minute: "04",
                    second: "05",
                    timezone: "+01:00"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2010-01-01T00:00:00Z");
        });

        it("should compile manual time for a year, month, day, hour, minute, second and timezone with dst source", function*() {
            let sources = {
                manual: {
                    year: "2009",
                    month: "06",
                    day: "02",
                    hour: [ "03", "09" ],
                    minute: "04",
                    second: "05",
                    timezone: "+01:00"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2009-06-02T03:00:00Z");
        });

        it("should compile manual time for a year, month, day, hour, minute, second and timezone #2 with dst source", function*() {
            let sources = {
                manual: {
                    year: "2008",
                    month: "06",
                    day: "02",
                    hour: [ "03", "12" ],
                    minute: "04",
                    second: [ "05", "09" ],
                    timezone: "+02:30"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2008-06-02T03:00:00Z");
        });

        it("should compile manual time for a year, month, day, hour, minute, second and timezone #3 with dst source", function*() {
            let sources = {
                manual: {
                    year: "2007",
                    month: "06",
                    day: [ "02", "12" ],
                    hour: "03",
                    minute: "04",
                    second: "05",
                    timezone: "-01:00"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2007-06-02T00:00:00Z");
        });

        it("should compile manual time for a year, month, day, hour, minute, second and timezone #3 without dst source", function*() {
            let sources = {
                manual: {
                    year: "2007",
                    month: "01",
                    day: "02",
                    hour: "03",
                    minute: "04",
                    second: [ "05", "59" ],
                    timezone: "-01:00"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "manual");
            assert.equal(compiled.quality, "fuzzy");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2007-01-02T03:04:05Z");
        });
    });

    describe("GPS", () => {
        it("should compile gps time for a year, month, day, hour, minute, second", function*() {
            let sources = {
                gps: {
                    year: "2006",
                    month: "06",
                    day: "02",
                    hour: "06",
                    minute: "54",
                    second: "05",
                    timezone: "+00:00"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "gps");
            assert.equal(compiled.quality, "utc");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2006-06-02T06:54:05Z");
        });
    });

    describe("Device type unknown", () => {
        it("should compile device time without DST", function*() {
            let sources = {
                device: {
                    year: "2006",
                    month: "06",
                    day: "02",
                    hour: "06",
                    minute: "54",
                    second: "05",
                    deviceAutoDst: false,
                    deviceType: "unknown"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "device");
            assert.equal(compiled.quality, "local");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2006-06-02T06:54:05Z");
        });

        it("should compile device time with DST", function*() {
            let sources = {
                device: {
                    year: "2006",
                    month: "06",
                    day: "02",
                    hour: "06",
                    minute: "54",
                    second: "05",
                    deviceAutoDst: true,
                    deviceType: "unknown"
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "device");
            assert.equal(compiled.quality, "local");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2006-06-02T05:54:05Z");
        });
    });

    describe("Device type offset_fixed", () => {
        it("should compile device time without DST in DST time frame", function*() {
            let sources = {
                device: {
                    year: "2006",
                    month: "06",
                    day: "02",
                    hour: "06",
                    minute: "54",
                    second: "05",
                    deviceAutoDst: false,
                    deviceType: "offset_fixed",
                    deviceUtcOffset: 3600
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "device");
            assert.equal(compiled.quality, "utc");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2006-06-02T05:54:05Z");
        });

        it("should compile device time without DST outside DST timeframe", function*() {
            let sources = {
                device: {
                    year: "2006",
                    month: "01",
                    day: "02",
                    hour: "06",
                    minute: "54",
                    second: "05",
                    deviceAutoDst: false,
                    deviceType: "offset_fixed",
                    deviceUtcOffset: 3600
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "device");
            assert.equal(compiled.quality, "utc");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2006-01-02T05:54:05Z");
        });

        it("should compile device time with DST in DST time frame", function*() {
            let sources = {
                device: {
                    year: "2006",
                    month: "06",
                    day: "02",
                    hour: "06",
                    minute: "54",
                    second: "05",
                    deviceAutoDst: true,
                    deviceType: "offset_fixed",
                    deviceUtcOffset: 3600 * 2
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "device");
            assert.equal(compiled.quality, "utc");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2006-06-02T03:54:05Z");
        });

        it("should compile device time with DST outside DST timeframe", function*() {
            let sources = {
                device: {
                    year: "2006",
                    month: "01",
                    day: "02",
                    hour: "06",
                    minute: "54",
                    second: "05",
                    deviceAutoDst: true,
                    deviceType: "offset_fixed",
                    deviceUtcOffset: 3600
                }
            };

            let compiled = yield api.time.compile(sources);

            assert.equal(compiled.source, "device");
            assert.equal(compiled.quality, "utc");
            assert.equal(moment.utc(compiled.timestamp * 1000).format(), "2006-01-02T05:54:05Z");
        });
    });

    // TODO: Test device with offset_relative_to_position with different deviceAutoDst
});
