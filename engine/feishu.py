"""飞书多维表写入：tenant_access_token -> bitable records。"""
import json

import httpx


class FeishuWriter:
    def __init__(self, app_id, app_secret, app_token, table_id):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self._token = None

    def _get_token(self):
        resp = httpx.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书 token 获取失败：{data.get('msg')}")
        self._token = data["tenant_access_token"]
        return self._token

    def write_record(self, record):
        token = self._token or self._get_token()
        fields = {
            "时间": record.get("time", ""),
            "岗位": record.get("position_name", ""),
            "候选人": record.get("name", ""),
            "职位": record.get("position", ""),
            "公司": record.get("company", ""),
            "匹配分": str(record.get("score", "")),
            "原因": record.get("reason", ""),
            "结果": record.get("result", ""),
            "方式": record.get("mode", ""),
        }
        resp = httpx.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={"fields": fields},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书写入失败：{data.get('msg')}")
        return data


def write_feishu(feishu_cfg, record):
    w = FeishuWriter(
        feishu_cfg.get("app_id", ""),
        feishu_cfg.get("app_secret", ""),
        feishu_cfg.get("app_token", ""),
        feishu_cfg.get("table_id", ""),
    )
    return w.write_record(record)
