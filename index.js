const axios = require("axios");
const express = require("express");
const { createTrendingService } = require("./trending");

function buildUrl(time, language) {
    return "https://github.com/trending" + (!!language ? "/" + language : "") + "?since=" + time; // 拼接请求的页面链接
}

// 真实上游请求：网络错误或非 2xx 时 axios 会 reject
async function fetchPage(time, language) {
    const response = await axios.get(buildUrl(time, language));
    return response.data;
}

function createApp(service) {
    const app = express();

    const handle = async (req, res) => {
        const result = await service.getList(req.params.time, req.params.language); // 发起抓取（带缓存与结局标记）
        res.status(result.status).json(result.body); // 数据返回
    };

    app.get("/list/:time/:language", handle);
    app.get("/list/:time", handle);

    app.get("/", async (req, res) => {
        const result = await service.getList("daily"); // 发起抓取
        res.status(result.status).json(result.body); // 数据返回
    });

    return app;
}

const service = createTrendingService({ fetchPage: fetchPage });
const app = createApp(service);

if (require.main === module) {
    app.listen(3000, () => console.log("Listening on port 3000!")); // 监听3000端口
}

module.exports = { createApp: createApp, buildUrl: buildUrl, fetchPage: fetchPage, app: app, service: service };
