package managers

import (
	"crawlab-lite/config"
	"crawlab-lite/dao"
	"crawlab-lite/database"
	"crawlab-lite/models"
	"crawlab-lite/utils"
	"fmt"
	"io/ioutil"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/apex/log"
	uuid "github.com/satori/go.uuid"
	"github.com/spf13/viper"
)

// initExecutorTestDB 初始化配置与 KV 数据库（go test 的工作目录为包目录）
func initExecutorTestDB(t *testing.T) {
	t.Helper()
	cfgPath := "../config.yml"
	if _, err := os.Stat(cfgPath); err != nil {
		cfgPath = "config.yml"
	}
	if err := config.InitConfig(cfgPath); err != nil {
		log.Error("Init config error:" + err.Error())
		t.Fatalf("init config error: %v", err)
	}
	if database.MainDB == nil {
		if err := database.InitKvDB(); err != nil {
			t.Fatalf("init kv database error: %v", err)
		}
	}
}

// writeSpiderVersion 直接在磁盘与数据库中写入一个爬虫版本，模拟成功上传后的状态
func writeSpiderVersion(t *testing.T, spiderId uuid.UUID, marker string) *models.SpiderVersion {
	t.Helper()
	versionId := uuid.NewV4()
	relPath := filepath.Join(spiderId.String(), versionId.String())
	absPath := filepath.Join(viper.GetString("spider.path"), relPath)
	if err := os.MkdirAll(absPath, os.ModePerm); err != nil {
		t.Fatalf("mkdir version path error: %v", err)
	}
	if err := ioutil.WriteFile(filepath.Join(absPath, "main.py"),
		[]byte(fmt.Sprintf("# %s\nprint('executor test')\n", marker)), os.ModePerm); err != nil {
		t.Fatalf("write version file error: %v", err)
	}

	// 计算该版本文件的 MD5，与上传流程一样以版本内容生成标识
	version := &models.SpiderVersion{
		Id:       versionId,
		SpiderId: spiderId,
		Path:     relPath,
	}
	version.MD5 = marker + "-md5"

	if err := dao.WriteTx(database.MainDB, func(tx dao.Tx) error {
		return tx.InsertSpiderVersion(version)
	}); err != nil {
		t.Fatalf("insert spider version error: %v", err)
	}
	return version
}

// TestQueryLatestSpiderVersion 验证任务执行器能够按最新版本找到刚上传的版本及其磁盘目录
func TestQueryLatestSpiderVersion(t *testing.T) {
	initExecutorTestDB(t)

	spiderId := uuid.NewV4()
	spider := &models.Spider{
		Id:          spiderId,
		Name:        "executor_spider_" + fmt.Sprintf("%d", time.Now().UnixNano()),
		Description: "executor latest version test",
	}
	if err := dao.WriteTx(database.MainDB, func(tx dao.Tx) error {
		return tx.InsertSpider(spider)
	}); err != nil {
		t.Fatalf("insert spider error: %v", err)
	}
	t.Cleanup(func() {
		_ = dao.WriteTx(database.MainDB, func(tx dao.Tx) error {
			return tx.DeleteSpider(spiderId)
		})
		_ = os.RemoveAll(filepath.Join(viper.GetString("spider.path"), spiderId.String()))
	})

	// 写入第一个版本
	first := writeSpiderVersion(t, spiderId, "v1")
	// 确保两个版本的创建时间存在可区分的先后顺序
	time.Sleep(10 * time.Millisecond)
	// 写入第二个版本（最新版本）
	second := writeSpiderVersion(t, spiderId, "v2")

	// 执行器查询最新版本
	latest, err := queryLatestSpiderVersion(spiderId)
	if err != nil {
		t.Fatalf("query latest spider version error: %v", err)
	}
	if latest == nil {
		t.Fatal("latest spider version should not be nil")
	}
	if latest.Id != second.Id {
		t.Fatalf("executor should resolve the latest version, want %s, got %s", second.Id, latest.Id)
	}
	if latest.Id == first.Id {
		t.Fatal("latest version must not be the older first version")
	}

	// 执行器据此拼接的工作目录必须真实存在
	cwd := filepath.Join(viper.GetString("spider.path"), latest.Path)
	if !utils.PathExist(cwd) {
		t.Fatalf("executor working directory not exist: %s", cwd)
	}
}
