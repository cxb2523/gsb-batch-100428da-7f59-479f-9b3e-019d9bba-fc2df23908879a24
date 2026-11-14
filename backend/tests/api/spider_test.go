package api

import (
	"archive/zip"
	"bytes"
	"crawlab-lite/constants"
	"crawlab-lite/dao"
	"crawlab-lite/database"
	"crawlab-lite/results"
	"crawlab-lite/utils"
	"encoding/json"
	"fmt"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/spf13/viper"
)

// loginForTest 通过实际挂载的 /login 路由登录并返回 Token
func loginForTest(t *testing.T, app http.Handler) (string, error) {
	t.Helper()
	user := dao.GetUser()
	if user == nil {
		return "", fmt.Errorf("user does not exist")
	}
	values := map[string]string{"username": user.Username, "password": user.Password}
	req, err := PostJson("/login", values)
	if err != nil {
		return "", err
	}
	w := httptest.NewRecorder()
	app.ServeHTTP(w, req)
	resp := GetResponse(w.Body)
	if w.Code != http.StatusOK || resp.Code != http.StatusOK {
		return "", fmt.Errorf("login failed, code: %d, message: %s", w.Code, resp.Message)
	}
	token, ok := resp.Data.(string)
	if !ok || token == "" {
		return "", fmt.Errorf("login response token invalid")
	}
	return token, nil
}

// buildValidZip 在内存中构造一个合法的 zip 包，内含一个可执行脚本
func buildValidZip(t *testing.T) []byte {
	t.Helper()
	buf := new(bytes.Buffer)
	zw := zip.NewWriter(buf)
	w, err := zw.Create("main.py")
	if err != nil {
		t.Fatalf("create zip entry error: %v", err)
	}
	if _, err := w.Write([]byte("# auto generated test spider\nprint('hello crawlab')\n")); err != nil {
		t.Fatalf("write zip entry error: %v", err)
	}
	if err := zw.Close(); err != nil {
		t.Fatalf("close zip error: %v", err)
	}
	return buf.Bytes()
}

// buildCorruptZip 构造一个扩展名为 .zip 但内容已损坏的包
func buildCorruptZip() []byte {
	return []byte("this is definitely not a valid zip archive payload")
}

// newMultipartRequest 构造带文件字段的 multipart 请求，extra 用于附带普通表单字段
func newMultipartRequest(t *testing.T, url string, filename string, content []byte, extra map[string]string) *http.Request {
	t.Helper()
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)

	part, err := writer.CreateFormFile("file", filename)
	if err != nil {
		t.Fatalf("create form file error: %v", err)
	}
	if _, err := part.Write(content); err != nil {
		t.Fatalf("write form file error: %v", err)
	}

	for k, v := range extra {
		if err := writer.WriteField(k, v); err != nil {
			t.Fatalf("write field %s error: %v", k, err)
		}
	}
	if err := writer.Close(); err != nil {
		t.Fatalf("close multipart writer error: %v", err)
	}

	req := httptest.NewRequest(http.MethodPost, url, body)
	req.Header.Set("Content-Type", writer.FormDataContentType())
	return req
}

func doRequest(t *testing.T, app http.Handler, req *http.Request, token string) (int, *results.ResponseBody) {
	t.Helper()
	if token != "" {
		req.Header.Set(constants.AuthHeader, token)
	}
	w := httptest.NewRecorder()
	app.ServeHTTP(w, req)

	resp := &results.ResponseBody{}
	if err := json.Unmarshal(w.Body.Bytes(), resp); err != nil {
		t.Fatalf("decode response error: %v, body: %s", err, w.Body.String())
	}
	return w.Code, resp
}

// spiderVersionTotal 通过版本列表接口获取版本数量
func spiderVersionTotal(t *testing.T, app http.Handler, token, spiderId string) int {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/spiders/"+spiderId+"/versions", nil)
	code, resp := doRequest(t, app, req, token)
	if code != http.StatusOK {
		t.Fatalf("query version list failed, code: %d, message: %s", code, resp.Message)
	}
	raw, err := json.Marshal(resp.Data)
	if err != nil {
		t.Fatalf("marshal version data error: %v", err)
	}
	var data struct {
		List  []json.RawMessage `json:"list"`
		Total int               `json:"total"`
	}
	if err := json.Unmarshal(raw, &data); err != nil {
		t.Fatalf("unmarshal version data error: %v", err)
	}
	return data.Total
}

// spiderExists 通过爬虫列表接口判断指定名称的爬虫是否存在
func spiderExists(t *testing.T, app http.Handler, token, name string) bool {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/spiders", nil)
	code, resp := doRequest(t, app, req, token)
	if code != http.StatusOK {
		t.Fatalf("query spider list failed, code: %d", code)
	}
	raw, err := json.Marshal(resp.Data)
	if err != nil {
		t.Fatalf("marshal spider data error: %v", err)
	}
	var data struct {
		List  []results.Spider `json:"list"`
		Total int              `json:"total"`
	}
	if err := json.Unmarshal(raw, &data); err != nil {
		t.Fatalf("unmarshal spider data error: %v", err)
	}
	for _, s := range data.List {
		if s.Name == name {
			return true
		}
	}
	return false
}

func deleteSpider(t *testing.T, app http.Handler, token, spiderId string) {
	t.Helper()
	req := httptest.NewRequest(http.MethodDelete, "/spiders/"+spiderId, nil)
	if code, resp := doRequest(t, app, req, token); code != http.StatusOK {
		t.Fatalf("delete spider %s failed, code: %d, message: %s", spiderId, code, resp.Message)
	}
}

// TestSpiderUploadFlow 验收爬虫上传/创建流程的失败回滚与正常路径
func TestSpiderUploadFlow(t *testing.T) {
	app := InitTestApp()
	if err := database.InitKvDB(); err != nil {
		t.Fatalf("init kv database error: %v", err)
	}

	token, err := loginForTest(t, app)
	if err != nil {
		t.Fatalf("login error: %v", err)
	}

	validZip := buildValidZip(t)
	corruptZip := buildCorruptZip()
	suffix := strconv.FormatInt(time.Now().UnixNano(), 36)

	spiderBase := filepath.Join(viper.GetString("spider.path"))
	tmpBase := filepath.Join(viper.GetString("other.tmppath"))

	// 场景 1：正常创建爬虫（随附首个版本），应成功并生成 1 个版本
	normalName := "as_normal_" + suffix
	req := newMultipartRequest(t, "/spiders", "spider.zip", validZip, map[string]string{
		"name":        normalName,
		"description": "acceptance test normal spider",
	})
	code, resp := doRequest(t, app, req, token)
	if code != http.StatusOK || resp.Code != http.StatusOK {
		t.Fatalf("create spider failed, code: %d, message: %s", code, resp.Message)
	}
	normalRaw, err := json.Marshal(resp.Data)
	if err != nil {
		t.Fatalf("marshal spider result error: %v", err)
	}
	var normalSpider results.Spider
	if err := json.Unmarshal(normalRaw, &normalSpider); err != nil {
		t.Fatalf("unmarshal spider result error: %v", err)
	}
	normalId := normalSpider.Id.String()
	defer deleteSpider(t, app, token, normalId)

	if total := spiderVersionTotal(t, app, token, normalId); total != 1 {
		t.Fatalf("expected 1 version after create, got %d", total)
	}

	// 场景 2：重复上传同一个包，应被拒绝且版本数不变
	req = newMultipartRequest(t, "/spiders/"+normalId+"/versions", "spider.zip", validZip, nil)
	code, resp = doRequest(t, app, req, token)
	if code != http.StatusBadRequest {
		t.Fatalf("duplicate upload should be rejected with 400, got %d", code)
	}
	if !strings.Contains(resp.Message, "already exists") {
		t.Fatalf("duplicate upload should return distinguishable duplicate error, got: %s", resp.Message)
	}
	if total := spiderVersionTotal(t, app, token, normalId); total != 1 {
		t.Fatalf("version count should remain 1 after duplicate upload, got %d", total)
	}
	// 重复上传不应残留临时目录
	if entries, _ := ioutilReadDirNames(filepath.Join(tmpBase, normalId)); len(entries) != 0 {
		t.Fatalf("temp directory should be cleaned after duplicate upload, residual: %v", entries)
	}

	// 场景 3：上传损坏包，应返回解压失败错误，版本列表不变且无解压目录残留
	req = newMultipartRequest(t, "/spiders/"+normalId+"/versions", "broken.zip", corruptZip, nil)
	code, resp = doRequest(t, app, req, token)
	if code != http.StatusBadRequest {
		t.Fatalf("corrupt upload should be rejected with 400, got %d", code)
	}
	if !strings.Contains(resp.Message, "unzip failed") {
		t.Fatalf("corrupt upload should return distinguishable unzip error, got: %s", resp.Message)
	}
	if total := spiderVersionTotal(t, app, token, normalId); total != 1 {
		t.Fatalf("version list should remain unchanged after corrupt upload, got %d versions", total)
	}
	// spider 目录下只能有成功上传的那个版本目录，不应残留本次解压目录
	spiderDirEntries, err := ioutilReadDirNames(filepath.Join(spiderBase, normalId))
	if err != nil {
		t.Fatalf("read spider dir error: %v", err)
	}
	if len(spiderDirEntries) != 1 {
		t.Fatalf("spider dir should contain only the valid version, got: %v", spiderDirEntries)
	}
	// 损坏包上传同样不应残留临时目录
	if entries, _ := ioutilReadDirNames(filepath.Join(tmpBase, normalId)); len(entries) != 0 {
		t.Fatalf("temp directory should be cleaned after corrupt upload, residual: %v", entries)
	}

	// 场景 4：正常上传第二个（不同的）版本，应成功；任务执行器按最新版本能找到该版本
	secondZip := buildValidZipWithMarker(t, "second-version-marker")
	req = newMultipartRequest(t, "/spiders/"+normalId+"/versions", "spider2.zip", secondZip, nil)
	code, resp = doRequest(t, app, req, token)
	if code != http.StatusOK || resp.Code != http.StatusOK {
		t.Fatalf("upload second version failed, code: %d, message: %s", code, resp.Message)
	}
	versionRaw, err := json.Marshal(resp.Data)
	if err != nil {
		t.Fatalf("marshal version result error: %v", err)
	}
	var secondVersion results.SpiderVersion
	if err := json.Unmarshal(versionRaw, &secondVersion); err != nil {
		t.Fatalf("unmarshal version result error: %v", err)
	}
	if total := spiderVersionTotal(t, app, token, normalId); total != 2 {
		t.Fatalf("expected 2 versions after second upload, got %d", total)
	}
	// 最新版本路径在磁盘上必须存在（执行器即按该路径定位工作目录）
	latestCwd := filepath.Join(spiderBase, secondVersion.Path)
	if !utils.PathExist(latestCwd) {
		t.Fatalf("latest version directory not found for executor: %s", latestCwd)
	}
	// 成功上传后临时目录同样被清理
	if entries, _ := ioutilReadDirNames(filepath.Join(tmpBase, normalId)); len(entries) != 0 {
		t.Fatalf("temp directory should be cleaned after successful upload, residual: %v", entries)
	}

	// 场景 5：创建爬虫时随附版本上传失败（损坏包），爬虫列表不应出现该爬虫
	failedName := "as_failed_" + suffix
	spiderDirsBefore, _ := ioutilReadDirNames(spiderBase)
	tmpDirsBefore, _ := ioutilReadDirNames(tmpBase)
	req = newMultipartRequest(t, "/spiders", "broken.zip", corruptZip, map[string]string{
		"name":        failedName,
		"description": "acceptance test failed create",
	})
	code, resp = doRequest(t, app, req, token)
	if code != http.StatusBadRequest {
		t.Fatalf("create with corrupt package should fail with 400, got %d", code)
	}
	if !strings.Contains(resp.Message, "unzip failed") {
		t.Fatalf("failed create should surface unzip error, got: %s", resp.Message)
	}
	if spiderExists(t, app, token, failedName) {
		t.Fatalf("spider %s should not exist after failed first version upload", failedName)
	}
	// 回滚后 spider 根目录与临时根目录均不应新增任何目录
	spiderDirsAfter, _ := ioutilReadDirNames(spiderBase)
	if len(spiderDirsAfter) != len(spiderDirsBefore) {
		t.Fatalf("no spider directory should remain after failed create, before: %v, after: %v",
			spiderDirsBefore, spiderDirsAfter)
	}
	tmpDirsAfter, _ := ioutilReadDirNames(tmpBase)
	if len(tmpDirsAfter) != len(tmpDirsBefore) {
		t.Fatalf("no temp directory should remain after failed create, before: %v, after: %v",
			tmpDirsBefore, tmpDirsAfter)
	}
}

// buildValidZipWithMarker 构造带不同内容的合法 zip 包，保证 MD5 与首个包不同
func buildValidZipWithMarker(t *testing.T, marker string) []byte {
	t.Helper()
	buf := new(bytes.Buffer)
	zw := zip.NewWriter(buf)
	w, err := zw.Create("main.py")
	if err != nil {
		t.Fatalf("create zip entry error: %v", err)
	}
	content := fmt.Sprintf("# marker: %s\nprint('hello again')\n", marker)
	if _, err := w.Write([]byte(content)); err != nil {
		t.Fatalf("write zip entry error: %v", err)
	}
	if err := zw.Close(); err != nil {
		t.Fatalf("close zip error: %v", err)
	}
	return buf.Bytes()
}

// ioutilReadDirNames 包装目录读取，返回子项名称列表，目录不存在时返回错误
func ioutilReadDirNames(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	return names, nil
}
