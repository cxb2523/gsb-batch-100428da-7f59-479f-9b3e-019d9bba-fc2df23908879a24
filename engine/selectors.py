"""选择器配置（集中管理，页面改版时只需改这里）。

⚠️ BOSS 直聘前端改版频繁，若定位失败请用「检查页面」功能校准。
"""

SELECTORS = {
    # 页面级
    "page": {
        "recommend_list": [".candidate-card-wrap", ".recommend-list", ".search-result-list",
                           "[class*='recommend-list']"],
        "candidate_row": [".list-row", ".recommend-list tr", "tr[class*='row']", "li[class*='row']"],
        "candidate_card": [".candidate-card-wrap", ".recommend-card", ".job-card",
                           "li[class*='card']", "div[class*='card']"],
        "next_page": [".pagination-next", "button[class*='next']", "[class*='pagination'] [class*='next']"],
        "load_more": [".load-more", "button[class*='more']"],
        "list_mode_marker": [".list-mode", "[class*='list-mode']", ".list-table", "table[class*='list']"],
        "login_hint": [".login-box", ".qrcode", "input[type='password']", "[class*='login']"],
        # 新版招聘端：左侧岗位筛选器
        "job_item": [".job-item", "[class*='job-item']", "[class*='position-item']"],
    },
    # 候选人行内字段
    "candidate": {
        "name": [".name", "[class*='name']", "td:nth-child(1)"],
        "age": [".age", "[class*='age']"],
        "gender": [".gender", "[class*='gender']", "[class*='sex']"],
        "education": [".education", "[class*='education']", "[class*='edu']"],
        "work_years": [".work-years", "[class*='work-year']"],
        "school": [".school", "[class*='school']", "[class*='university']"],
        "position": [".position", "[class*='position']", "[class*='job-title']"],
        "company": [".company", "[class*='company']", "[class*='brand']"],
        "salary": [".salary", "[class*='salary']", "[class*='money']"],
        "tags": [".tag", "[class*='tag']", "[class*='skill']"],
        "summary": [".summary", "[class*='summary']", "[class*='desc']"],
        "viewed": ["[class*='viewed']", "[class*='read']"],
        "greet_button": ["button[class*='greet']", "button[class*='hi']", "a[class*='greet']", ".greet"],
    },
    # 筛选面板
    "filter": {
        "panel": ["[class*='filter-panel']", "[class*='condition']", "[class*='filter']"],
        "confirm": ["button[class*='confirm']", "button[class*='apply']", "[class*='sure']", ".apply"],
        "activity": ["[class*='activity']", "[class*='active']"],
        "school": ["[class*='school']", "[class*='college']"],
        "experience": ["[class*='experience']", "[class*='work-year']"],
        "education": ["[class*='education']", "[class*='degree']"],
        "intention": ["[class*='intention']", "[class*='intent']"],
        "hopping": ["[class*='hopping']", "[class*='jump']", "[class*='frequency']"],
        "gender": ["[class*='gender']"],
        "age": ["[class*='age']"],
        "option": "span, li, div, button, label",
    },
    # 打招呼弹窗
    "greet_dialog": {
        "panel": ["[class*='dialog']", "[class*='modal']"],
        "message_input": ["textarea", "[class*='message'] textarea"],
        "send_button": ["button[class*='send']", "button[class*='submit']", "button[class*='ok']"],
    },
    # 沟通页（/web/chat/index）：发常用语 + 求简历
    "chat": {
        "candidate_item": [
            ".geek-name",
            "[class*='geek-item']",
            "li:has(.geek-name)",
            "div:has(> .geek-name)",
            ".geek-item",
            ".name"
        ],
        "candidate_name": [".geek-name", "[class*='geek-name']", ".name"],
        "message_input": [
            "textarea",
            "[class*='chat-input'] textarea",
            "div[class*='chat'] textarea",
            "[contenteditable='true']",
            "[class*='chat-input'] [contenteditable]",
            "div[class*='input'] [contenteditable]"
        ],
        "send_button": [
            "button[class*='send']",
            "button[class*='submit']",
            "button:has-text('发送')",
            "[class*='btn']:has-text('发送')",
            "[class*='send-btn']",
            "[aria-label*='发送']"
        ],
        "quick_phrase": ["[class*='quick']", "[class*='phrase']", "button:has-text('常用语')"],
        "request_resume": [
            "button:text-is('求简历')",
            "button:text-is('索要简历')",
            "button:text-is('请求简历')",
            "li:text-is('求简历')",
            "span:text-is('求简历')",
            "div:text-is('求简历')",
            "a:text-is('索要简历')",
            "[class*='menu-item']:text-is('求简历')",
            ":text-is('求简历')"
        ],
        "resume_confirm_dialog": [
            "[class*='dialog']:has-text('简历')",
            "[class*='modal']:has-text('简历')",
            "[class*='confirm']:has-text('简历')",
            "[class*='popup']:has-text('简历')"
        ],
        "resume_confirm_button": [
            "button:text-is('确认')",
            "button:text-is('确定')",
            ":text-is('确定')",
            "button:has-text('确认')",
            "button:has-text('好的')",
            "[class*='confirm'] button",
            "[class*='dialog'] button"
        ],
        # 候选人行悬停操作图标（新版页面入口）
        "resume_operate_icon": [
            ".icon-operate",
            "[class*='list-operate']",
            "[class*='operate'] img"
        ],
        # 点击操作图标后弹出的菜单项
        "resume_menu_item": [
            "li:text-is('求简历')",
            "span:text-is('求简历')",
            "div:text-is('求简历')",
            "[class*='menu-item']:text-is('求简历')",
            "[class*='menu'] :text-is('求简历')",
            ":text-is('求简历')"
        ],
        # 在线简历入口与简历面板
        "online_resume_tab": [
            "button:text-is('在线简历')",
            "li:text-is('在线简历')",
            "span:text-is('在线简历')",
            "div:text-is('在线简历')",
            ":text-is('在线简历')",
            "[class*='resume']:has-text('在线简历')"
        ],
        "resume_panel": [
            "[class*='geek-resume']",
            "[class*='resume-detail']",
            "[class*='resume-panel']",
            "[class*='resume'] [class*='info']",
            "[class*='geek-detail']"
        ],
    },
}
