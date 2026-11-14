# 东北大学修改了统一身份认证登录的逻辑，不再限制过多的登录次数限制。
class UnionAuthError(Exception):
    pass


# 统一身份认证页面抓取（SSO_prepare 与公钥提取）失败的基类。
# message 是面向用户的可读提示，clue 是用于定位问题的页面/脚本线索（标题、脚本地址、片段等）。
class SSOPrepareError(Exception):
    def __init__(self, message: str, clue: str = None):
        super().__init__(message)
        self.message = message
        self.clue = clue

    def __str__(self):
        if self.clue:
            return f"{self.message}（线索：{self.clue}）"
        return self.message


# 在统一身份认证页面中找不到登录表单（id 为 loginForm 的 form）。
class LoginFormNotFoundError(SSOPrepareError):
    def __init__(self, clue: str = None):
        super().__init__("无法在统一身份认证页面中找到登录表单，页面结构可能已变更", clue)


# 登录表单中缺少必需字段（lt / execution / action）。
class LoginFormFieldMissingError(SSOPrepareError):
    def __init__(self, field_name: str, clue: str = None):
        super().__init__(f"登录表单中缺少必需字段 {field_name}，页面结构可能已变更", clue)
        self.field_name = field_name


# 在统一身份认证页面中找不到登录脚本（login_neu.js / login_neu_new.js）。
class LoginScriptNotFoundError(SSOPrepareError):
    def __init__(self, clue: str = None):
        super().__init__("无法在统一身份认证页面中找到登录脚本（login_neu.js），脚本可能已改名", clue)


# 登录脚本中缺少 publicKeyStr，无法提取 RSA 公钥。
class PublicKeyNotFoundError(SSOPrepareError):
    def __init__(self, clue: str = None):
        super().__init__("无法在登录脚本中找到 publicKeyStr，无法提取 RSA 公钥", clue)


# 网络层临时性失败（超时、连接错误等）在有限次重试后仍然失败。
class SSONetworkError(SSOPrepareError):
    def __init__(self, description: str, attempts: int, last_error: Exception):
        super().__init__(f"{description}失败：已重试 {attempts} 次，网络仍然不可用，请检查网络连接后重试")
        self.description = description
        self.attempts = attempts
        self.last_error = last_error


# 抱歉！您的请求出现了异常，请稍后再试。
class SystemHintError(Exception):
    pass


# 明确且已知的后端错误，比如 Uncaught exception 'RedisException' with message 'read error on connection'之类的东西
class BackendError(Exception):
    def __init__(self, page):
        self.page = page


class UnknownPageError(Exception):
    def __init__(self, page):
        self.page = page


# 在尝试登录之后，页面返回智慧东大，但是并没有提示密码错误次数的信息，这种奇怪的错误在这里报告。
class IntimateUnionAuthPageError(UnknownPageError):
    def __init__(self, page):
        self.page = page


# 登出指定设备需要CAS验证。
class RequestNeedCASAuthenticError(Exception):
    pass


# 在查询用户已经消费的流量或在线时长的时候，服务器返回not online错误。
class UserNotOnlineError(Exception):
    pass