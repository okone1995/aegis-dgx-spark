"""edu-lite 功能测试套件 —— verify 门禁的"功能不破坏"证据（SPEC §11②）。

用法:
  EDU_BASE=http://127.0.0.1:8081 EDU_INSTANCE=a pytest -q tests/test_functional.py
对 A/B 实例各跑一遍；修补前后都必须全绿。
"""
import io
import json
import os
import re

import pytest
import requests

BASE = os.environ.get("EDU_BASE", "http://127.0.0.1:8081")
INST = os.environ.get("EDU_INSTANCE", "a")
CFG = {
    "a": dict(search="q", avatar="avatar", sig="signature", imp="backup", cookie="edu_session"),
    "b": dict(search="keyword", avatar="picture", sig="motto", imp="restore", cookie="session_key"),
}[INST]

# 1x1 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da636460f8ff9f0001050102caca24b5"
    "0000000049454e44ae426082"
)


@pytest.fixture()
def sess():
    s = requests.Session()
    r = s.post(f"{BASE}/login", data={"username": "admin", "password": "admin123"},
               allow_redirects=False)
    assert r.status_code in (302, 303), f"login failed: {r.status_code}"
    return s


def test_news_list_and_benign_search(sess):
    r = sess.get(f"{BASE}/news")
    assert r.status_code == 200 and "期中考试" in r.text
    r = sess.get(f"{BASE}/news/search", params={CFG["search"]: "图书馆"})
    assert r.status_code == 200 and "图书馆" in r.text


def test_benign_search_no_result_is_clean(sess):
    r = sess.get(f"{BASE}/news/search", params={CFG["search"]: "不存在的关键字xyz"})
    assert r.status_code == 200 and "VMARK" not in r.text


def test_avatar_upload_valid_png(sess):
    r = sess.post(f"{BASE}/profile/avatar",
                  files={CFG["avatar"]: ("ok_avatar.png", io.BytesIO(PNG), "image/png")})
    assert r.status_code == 200
    # 从响应解析实际保存名（补丁启用随机重命名后文件名变化，不硬编码——行为断言而非实现断言）
    m = re.search(r"saved /uploads/([\w.\-]+)", r.text)
    assert m, f"no saved marker in: {r.text[:200]}"
    r2 = sess.get(f"{BASE}/uploads/{m.group(1)}")
    assert r2.status_code == 200 and r2.content == PNG


def test_signature_roundtrip(sess):
    marker = f"sig-{INST}-{id(object())}"
    r = sess.post(f"{BASE}/profile", data={CFG["sig"]: marker})
    assert r.status_code == 200 and r.json()["sig"] == marker  # 补丁后此处应转义（行为演进：xss_stored 门禁）
    r = sess.get(f"{BASE}/profile")
    assert marker in r.text


def test_avatar_url_legit_fetch(sess):
    # 合法用例 = 本地素材服务（127.0.0.1:30010）——全本地确定性，无公网依赖（D5 评审 P1-2）
    r = sess.post(f"{BASE}/profile/avatar-url",
                  data={{"a": "avatar_url", "b": "pic_url"}[INST]: "http://127.0.0.1:30010/avatar.png"})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["bytes"] > 0


def test_download_notice_file(sess):
    r = sess.get(f"{BASE}/download", params={{"a": "file", "b": "path"}[INST]: "notice.txt"})
    assert r.status_code == 200 and "校园站公告" in r.text


def test_user_edit_own_profile(sess):
    r = sess.post(f"{BASE}/admin/user-edit",
                  data={{"a": "uid", "b": "target"}[INST]: "1", CFG["sig"]: "自查更新"})
    assert r.status_code == 200 and r.json()["updated"] == "admin"


def test_admin_can_manage_others(sess):
    # idor 补丁的管理员例外：管理能力零回归（D5 评审 P1-1）
    r = sess.post(f"{BASE}/admin/user-edit",
                  data={{"a": "uid", "b": "target"}[INST]: "3", CFG["sig"]: "管理员例外的合法修改"})
    assert r.status_code == 200 and r.json()["updated"] == "bob"


def test_admin_export_import_roundtrip(sess):
    """合法备份导出→导入闭环。修补前 serialize 格式、修补后 json 格式，均应通过（自洽）。"""
    r = sess.get(f"{BASE}/admin/export")
    assert r.status_code == 200
    backup = r.json()["backup"]
    r = sess.post(f"{BASE}/admin/import", json={CFG["imp"]: backup})
    assert r.status_code == 200 and r.json().get("ok") is True


def test_unauthorized_admin_blocked():
    r = requests.get(f"{BASE}/admin/export")
    assert r.status_code == 403


def test_bad_login_rejected():
    r = requests.post(f"{BASE}/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
