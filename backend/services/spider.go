package services

import (
	"crawlab-lite/dao"
	"crawlab-lite/database"
	"crawlab-lite/forms"
	"crawlab-lite/models"
	"crawlab-lite/results"
	"crawlab-lite/utils"
	"errors"
	"fmt"
	. "github.com/ahmetb/go-linq/v3"
	"github.com/apex/log"
	"github.com/jinzhu/copier"
	uuid "github.com/satori/go.uuid"
	"github.com/spf13/viper"
	"io/ioutil"
	"os"
	"path/filepath"
)

// 上传版本时的业务错误，调用方据此区分失败类型
var (
	// ErrSpiderVersionDuplicated 重复版本：上传的压缩包与已有版本 MD5 相同
	ErrSpiderVersionDuplicated = errors.New("spider version already exists (duplicate package md5)")
	// ErrSpiderVersionUnzipFailed 解压失败：压缩包损坏或无法正常解压
	ErrSpiderVersionUnzipFailed = errors.New("spider version unzip failed")
)

func QuerySpiderPage(page forms.PageForm) (total int, resultList []*results.Spider, err error) {
	if err := dao.ReadTx(database.MainDB, func(tx dao.Tx) error {
		allSpiders, err := tx.SelectAllSpiders()
		if err != nil {
			return err
		}

		query := From(allSpiders).OrderByDescendingT(func(spider *models.Spider) int64 {
			return spider.CreateTs.UnixNano()
		}).Query
		total = query.Count()

		if page.PageNum > 0 && page.PageSize > 0 {
			start, end := page.Range()
			query = query.Skip(start).Take(end - start)
		}
		spiders := query.Results()

		cache := map[uuid.UUID]*models.Task{}
		for _, spider := range spiders {
			spider := spider.(*models.Spider)
			var result results.Spider
			if err := copier.Copy(&result, spider); err != nil {
				return err
			}
			var task *models.Task
			task, exists := cache[spider.Id]
			if !exists {
				tasks, err := tx.SelectTasksWhereSpiderId(spider.Id)
				if err != nil {
					return err
				}
				taskI := From(tasks).OrderByDescendingT(func(task *models.Task) int64 {
					return task.UpdateTs.UnixNano()
				}).First()
				if taskI != nil {
					task = taskI.(*models.Task)
					cache[spider.Id] = task
				}
			}
			if task != nil {
				result.LastRunTs = task.StartTs
				result.LastStatus = task.Status
				result.LastError = task.Error
			}
			resultList = append(resultList, &result)
		}
		return nil
	}); err != nil {
		return 0, nil, err
	}

	return total, resultList, nil
}

func QuerySpider(id uuid.UUID) (result *results.Spider, err error) {
	if err := dao.ReadTx(database.MainDB, func(tx dao.Tx) error {
		spider, err := tx.SelectSpider(id)
		if err != nil {
			return err
		}
		result = &results.Spider{}
		if err := copier.Copy(&result, spider); err != nil {
			return err
		}
		return nil
	}); err != nil {
		return nil, err
	}
	return result, nil
}

func AddSpider(form forms.SpiderForm) (result *results.Spider, err error) {
	spiderName := form.Name

	if err := dao.WriteTx(database.MainDB, func(tx dao.Tx) error {
		// 检查爬虫是否已存在
		spider, err := tx.SelectSpiderWhereName(spiderName)
		if err != nil {
			return err
		}
		if spider != nil {
			return errors.New("spider name already exists")
		}

		// 存储爬虫信息
		spider = &models.Spider{
			Name:        spiderName,
			Description: form.Description,
		}
		if err = tx.InsertSpider(spider); err != nil {
			return err
		}

		result = &results.Spider{}
		if err := copier.Copy(result, spider); err != nil {
			return err
		}
		return nil
	}); err != nil {
		return nil, err
	}

	return result, nil
}

// AddSpiderWithVersion 创建爬虫并同时上传首个版本。
// 若首个版本上传失败，回滚已写入的爬虫记录，保证不会留下没有任何版本的爬虫。
func AddSpiderWithVersion(form forms.SpiderForm) (result *results.Spider, err error) {
	result, err = AddSpider(form)
	if err != nil {
		return nil, err
	}

	// 随附的版本上传失败时，删除刚刚创建的爬虫记录及相关数据
	if _, versionErr := AddSpiderVersion(result.Id, form.SpiderUploadForm); versionErr != nil {
		if _, removeErr := RemoveSpider(result.Id); removeErr != nil {
			log.Errorf("rollback spider %s after version upload failed, remove error: %s (original error: %s)",
				result.Id.String(), removeErr.Error(), versionErr.Error())
		}
		return nil, versionErr
	}

	return result, nil
}

func RemoveSpider(id uuid.UUID) (res interface{}, err error) {
	if err := dao.WriteTx(database.MainDB, func(tx dao.Tx) error {
		// 检查爬虫是否存在
		if spider, err := tx.SelectSpider(id); err != nil {
			return err
		} else if spider == nil {
			return errors.New("spider not found")
		}

		// 删除爬虫
		if err = tx.DeleteSpider(id); err != nil {
			return err
		}

		// 删除爬虫的所有版本
		if err = tx.DeleteAllSpiderVersions(id); err != nil {
			return err
		}

		// 删除爬虫的所有任务
		if err = tx.DeleteTasksWhereSpiderId(id); err != nil {
			return err
		}

		// 删除爬虫的所有定时调度
		if err = tx.DeleteAllSchedulesWhereSpiderId(id); err != nil {
			return err
		}

		// 删除版本文件
		spiderDir := viper.GetString("spider.path")
		dirs, _ := ioutil.ReadDir(spiderDir)
		for _, dir := range dirs {
			if dir.IsDir() && dir.Name() == id.String() {
				if err := os.RemoveAll(filepath.Join(spiderDir, id.String())); err != nil {
					return err
				}
			}
		}

		// 删除日志
		logDir := filepath.Join(viper.GetString("log.path"), id.String())
		if utils.PathExist(logDir) {
			if err := os.RemoveAll(logDir); err != nil {
				return err
			}
		}

		return nil
	}); err != nil {
		return nil, err
	}

	return nil, nil
}

func QuerySpiderVersionPage(page forms.SpiderVersionPageForm) (total int, resultList []*results.SpiderVersion, err error) {
	if err := dao.ReadTx(database.MainDB, func(tx dao.Tx) error {
		allVersions, err := tx.SelectAllSpiderVersions(uuid.FromStringOrNil(page.SpiderId))
		if err != nil {
			return err
		}

		query := From(allVersions).OrderByDescendingT(func(version *models.SpiderVersion) int64 {
			return version.CreateTs.UnixNano()
		}).Query
		total = query.Count()

		if page.PageNum > 0 && page.PageSize > 0 {
			start, end := page.Range()
			query = query.Skip(start).Take(end - start)
		}
		versions := query.Results()

		for _, version := range versions {
			var result results.SpiderVersion
			if err := copier.Copy(&result, version); err != nil {
				return err
			}
			resultList = append(resultList, &result)
		}
		return nil
	}); err != nil {
		return 0, nil, err
	}

	return total, resultList, nil
}

func QuerySpiderVersion(spiderId uuid.UUID, versionId uuid.UUID) (result *results.SpiderVersion, err error) {
	if err := dao.ReadTx(database.MainDB, func(tx dao.Tx) error {
		version, err := tx.SelectSpiderVersion(spiderId, versionId)
		if err != nil {
			return err
		}
		result = &results.SpiderVersion{}
		if err := copier.Copy(&result, version); err != nil {
			return err
		}
		return nil
	}); err != nil {
		return nil, err
	}

	return result, nil
}

func AddSpiderVersion(spiderId uuid.UUID, form forms.SpiderUploadForm) (result *results.SpiderVersion, err error) {
	if err := dao.WriteTx(database.MainDB, func(tx dao.Tx) error {
		// 检查爬虫是否存在
		if spider, err := tx.SelectSpider(spiderId); err != nil {
			return err
		} else if spider == nil {
			return errors.New("spider not found")
		}

		uid := uuid.NewV4()

		// 本次上传独占的临时目录，避免多个上传共用同一目录时互相清理
		baseTmpPath := filepath.Join(viper.GetString("other.tmppath"), spiderId.String())
		tmpPath := filepath.Join(baseTmpPath, uid.String())
		zipPath := filepath.Join(tmpPath, uid.String()+".zip")

		// 解压目标路径（版本路径规则保持不变）
		version := &models.SpiderVersion{
			Id:       uid,
			SpiderId: spiderId,
			Path:     filepath.Join(spiderId.String(), uid.String()),
		}
		unzipPath := filepath.Join(viper.GetString("spider.path"), version.Path)

		// 无论成功失败，都清理本次产生的临时文件；
		// 失败时同时清理已创建的解压目录，成功时保留解压目录。
		success := false
		defer func() {
			_ = os.RemoveAll(tmpPath)
			// 临时父目录若已为空则一并删除，删除失败（如其他上传正在写入）可忽略
			_ = os.Remove(baseTmpPath)
			if !success {
				_ = os.RemoveAll(unzipPath)
			}
		}()

		formFile, err := form.File.Open()
		if err != nil {
			return err
		}
		defer formFile.Close()

		// 创建本次上传的临时目录
		if err := os.MkdirAll(tmpPath, os.ModePerm); err != nil {
			return err
		}

		// 保存压缩文件到临时目录
		if err := utils.SaveFile(formFile, zipPath); err != nil {
			return err
		}

		// 打开压缩文件
		zipFile, err := os.OpenFile(zipPath, os.O_CREATE|os.O_RDWR, os.ModePerm)
		if err != nil {
			return err
		}
		defer zipFile.Close()

		version.MD5 = utils.GetFileMD5(zipFile)

		// 通过文件 MD5 判断是否为重复的版本（去重规则保持不变）
		if _version, err := tx.SelectSpiderVersionWhereMD5(spiderId, version.MD5); err != nil {
			return err
		} else if _version != nil {
			return ErrSpiderVersionDuplicated
		}

		// 解压文件，失败时返回可识别的解压错误，临时文件与解压目录由 defer 清理
		if err := utils.Unzip(zipFile, unzipPath); err != nil {
			return fmt.Errorf("%w: %s", ErrSpiderVersionUnzipFailed, err.Error())
		}

		// 修改权限
		if err := os.Chmod(unzipPath, os.ModePerm); err != nil {
			return err
		}

		// 判断解压后外面是否还有一层目录
		fileList, err := ioutil.ReadDir(unzipPath)
		if err == nil && len(fileList) == 1 && fileList[0].IsDir() {
			version.Path = filepath.Join(version.Path, fileList[0].Name())
		}

		// 存储版本信息
		if err := tx.InsertSpiderVersion(version); err != nil {
			return err
		}

		result = &results.SpiderVersion{}
		if err := copier.Copy(&result, version); err != nil {
			return err
		}

		success = true
		return nil
	}); err != nil {
		return nil, err
	}

	return result, nil
}

func RemoveSpiderVersion(spiderId uuid.UUID, versionId uuid.UUID) (res interface{}, err error) {
	if err := dao.WriteTx(database.MainDB, func(tx dao.Tx) error {
		// 检查爬虫是否存在
		if spider, err := tx.SelectSpider(spiderId); err != nil {
			return err
		} else if spider == nil {
			return errors.New("spider not found")
		}

		// 查询版本信息
		version, err := tx.SelectSpiderVersion(spiderId, versionId)
		if err != nil {
			return err
		} else if version == nil {
			return errors.New("spider version not found")
		}

		// 删除爬虫版本信息
		if err = tx.DeleteSpiderVersion(spiderId, versionId); err != nil {
			return err
		}

		// 删除版本文件
		dirPath := viper.GetString("spider.path")
		if err := os.RemoveAll(filepath.Join(dirPath, version.SpiderId.String(), version.Id.String())); err != nil {
			return err
		}

		return nil
	}); err != nil {
		return nil, err
	}

	return nil, nil
}
