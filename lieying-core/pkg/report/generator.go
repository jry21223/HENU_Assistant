package report

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

type ReportGenerator struct {
	target      string
	vulns       []map[string]interface{}
	template    string
	outputDir   string
}

type ReportData struct {
	Title         string
	Target        string
	Date          string
	Author        string
	Team          string
	VulnCount     int
	CriticalCount int
	HighCount     int
	MediumCount   int
	LowCount      int
	Vulnerabilities []VulnData
}

type VulnData struct {
	ID          string
	Name        string
	Type        string
	Severity    string
	URL         string
	Description string
	Evidence    string
	Solution    string
	POC         string
}

func NewReportGenerator(target string, vulns []map[string]interface{}) *ReportGenerator {
	homeDir, _ := os.UserHomeDir()
	return &ReportGenerator{
		target:    target,
		vulns:     vulns,
		template:  "src",
		outputDir: filepath.Join(homeDir, ".lieying", "reports"),
	}
}

func (rg *ReportGenerator) SetTemplate(template string) {
	rg.template = template
}

func (rg *ReportGenerator) SetOutputDir(dir string) {
	rg.outputDir = dir
}

func (rg *ReportGenerator) Generate() (string, error) {
	fmt.Println("📄 正在生成报告...")

	if err := os.MkdirAll(rg.outputDir, 0755); err != nil {
		return "", err
	}

	content, err := rg.GenerateMarkdown()
	if err != nil {
		return "", err
	}

	timestamp := time.Now().Format("20060102_150405")
	filename := fmt.Sprintf("report_%s_%s.md", sanitizeFilename(rg.target), timestamp)
	filepath := filepath.Join(rg.outputDir, filename)

	if err := os.WriteFile(filepath, []byte(content), 0644); err != nil {
		return "", err
	}

	fmt.Printf("✅ 报告已生成: %s\n", filepath)
	return filepath, nil
}

func (rg *ReportGenerator) GenerateMarkdown() (string, error) {
	data := rg.prepareData()

	var content string
	var err error

	switch rg.template {
	case "src":
		content, err = rg.generateSRCReport(data)
	case "pentest":
		content, err = rg.generatePentestReport(data)
	case "internal":
		content, err = rg.generateInternalReport(data)
	default:
		content, err = rg.generateSRCReport(data)
	}

	if err != nil {
		return "", err
	}

	return content, nil
}

func (rg *ReportGenerator) prepareData() *ReportData {
	data := &ReportData{
		Title:           fmt.Sprintf("渗透测试报告 - %s", rg.target),
		Target:          rg.target,
		Date:            time.Now().Format("2006-01-02"),
		Author:          "昆仑安全实验室",
		Team:            "昆仑安全实验室(前逍遥安全实验室-逍遥)",
		Vulnerabilities: []VulnData{},
	}

	for i, vuln := range rg.vulns {
		vulnData := VulnData{
			ID:          fmt.Sprintf("VULN-%03d", i+1),
			Name:        getString(vuln, "name"),
			Type:        getString(vuln, "type"),
			Severity:    getString(vuln, "severity"),
			URL:         getString(vuln, "url"),
			Description: getString(vuln, "description"),
			Evidence:    getString(vuln, "evidence"),
			Solution:    rg.getSolution(getString(vuln, "type")),
			POC:         getString(vuln, "poc"),
		}
		data.Vulnerabilities = append(data.Vulnerabilities, vulnData)
		data.VulnCount++

		switch strings.ToLower(vulnData.Severity) {
		case "critical":
			data.CriticalCount++
		case "high":
			data.HighCount++
		case "medium":
			data.MediumCount++
		case "low":
			data.LowCount++
		}
	}

	return data
}

func (rg *ReportGenerator) generateSRCReport(data *ReportData) (string, error) {
	var sb strings.Builder

	sb.WriteString(fmt.Sprintf("# %s\n\n", data.Title))
	sb.WriteString("## 基本信息\n\n")
	sb.WriteString(fmt.Sprintf("- **目标**: %s\n", data.Target))
	sb.WriteString(fmt.Sprintf("- **测试日期**: %s\n", data.Date))
	sb.WriteString(fmt.Sprintf("- **提交者**: %s\n", data.Author))
	sb.WriteString(fmt.Sprintf("- **团队**: %s\n\n", data.Team))

	sb.WriteString("## 漏洞摘要\n\n")
	sb.WriteString(fmt.Sprintf("- **总漏洞数**: %d\n", data.VulnCount))
	sb.WriteString(fmt.Sprintf("- **严重**: %d\n", data.CriticalCount))
	sb.WriteString(fmt.Sprintf("- **高危**: %d\n", data.HighCount))
	sb.WriteString(fmt.Sprintf("- **中危**: %d\n", data.MediumCount))
	sb.WriteString(fmt.Sprintf("- **低危**: %d\n\n", data.LowCount))

	sb.WriteString("## 漏洞详情\n\n")

	for _, vuln := range data.Vulnerabilities {
		sb.WriteString(fmt.Sprintf("### %s - %s\n\n", vuln.ID, vuln.Name))
		sb.WriteString(fmt.Sprintf("**漏洞类型**: %s\n\n", vuln.Type))
		sb.WriteString(fmt.Sprintf("**严重级别**: %s\n\n", vuln.Severity))
		sb.WriteString(fmt.Sprintf("**漏洞URL**: %s\n\n", vuln.URL))
		sb.WriteString(fmt.Sprintf("**漏洞描述**: \n%s\n\n", vuln.Description))
		sb.WriteString(fmt.Sprintf("**证明**: \n%s\n\n", vuln.Evidence))
		if vuln.POC != "" {
			sb.WriteString(fmt.Sprintf("**POC**: \n```\n%s\n```\n\n", vuln.POC))
		}
		sb.WriteString(fmt.Sprintf("**修复建议**: \n%s\n\n", vuln.Solution))
		sb.WriteString("---\n\n")
	}

	sb.WriteString("## 免责声明\n\n")
	sb.WriteString("本报告仅供安全研究和漏洞修复参考使用，请勿用于非法用途。\n")

	return sb.String(), nil
}

func (rg *ReportGenerator) generatePentestReport(data *ReportData) (string, error) {
	var sb strings.Builder

	sb.WriteString(fmt.Sprintf("# %s\n\n", data.Title))
	sb.WriteString("## 执行摘要\n\n")
	sb.WriteString(fmt.Sprintf("本次渗透测试针对 **%s** 进行，共发现 **%d** 个安全漏洞。\n\n", 
		data.Target, data.VulnCount))
	sb.WriteString("### 风险评级\n\n")
	sb.WriteString(fmt.Sprintf("- 🔴 严重: %d\n", data.CriticalCount))
	sb.WriteString(fmt.Sprintf("- 🟠 高危: %d\n", data.HighCount))
	sb.WriteString(fmt.Sprintf("- 🟡 中危: %d\n", data.MediumCount))
	sb.WriteString(fmt.Sprintf("- 🟢 低危: %d\n\n", data.LowCount))

	sb.WriteString("## 测试范围\n\n")
	sb.WriteString(fmt.Sprintf("- 目标: %s\n", data.Target))
	sb.WriteString(fmt.Sprintf("- 测试时间: %s\n", data.Date))
	sb.WriteString("- 测试方法: 自动化扫描 + 手工验证\n\n")

	sb.WriteString("## 详细发现\n\n")

	for _, vuln := range data.Vulnerabilities {
		sb.WriteString(fmt.Sprintf("### %s [%s]\n\n", vuln.Name, vuln.Severity))
		sb.WriteString(fmt.Sprintf("**漏洞类型**: %s\n\n", vuln.Type))
		sb.WriteString(fmt.Sprintf("**URL**: %s\n\n", vuln.URL))
		sb.WriteString(fmt.Sprintf("**描述**: \n%s\n\n", vuln.Description))
		sb.WriteString(fmt.Sprintf("**证据**: \n%s\n\n", vuln.Evidence))
		sb.WriteString(fmt.Sprintf("**修复方案**: \n%s\n\n", vuln.Solution))
		sb.WriteString("---\n\n")
	}

	sb.WriteString("## 附录\n\n")
	sb.WriteString("### 测试工具\n\n")
	sb.WriteString("- 猎影渗透测试平台\n")
	sb.WriteString("- Nuclei\n")
	sb.WriteString("- 自定义扫描器\n\n")

	sb.WriteString(fmt.Sprintf("### 报告生成时间\n\n%s\n", time.Now().Format("2006-01-02 15:04:05")))

	return sb.String(), nil
}

func (rg *ReportGenerator) generateInternalReport(data *ReportData) (string, error) {
	return rg.generateSRCReport(data)
}

func (rg *ReportGenerator) getSolution(vulnType string) string {
	solutions := map[string]string{
		"SQL注入": "1. 使用参数化查询或预编译语句\n2. 对用户输入进行严格过滤和验证\n3. 使用ORM框架\n4. 最小权限原则，限制数据库用户权限",
		"XSS": "1. 对用户输入进行HTML编码\n2. 使用Content Security Policy (CSP)\n3. 对输出进行过滤\n4. 使用现代框架的自动转义功能",
		"文件上传": "1. 限制上传文件类型和大小\n2. 对上传文件进行重命名\n3. 将上传文件存储在非Web目录\n4. 使用文件类型检测而非仅依赖扩展名",
		"未授权访问": "1. 实施身份验证机制\n2. 配置访问控制列表\n3. 敏感接口添加权限检查\n4. 定期审计访问日志",
	}

	if solution, ok := solutions[vulnType]; ok {
		return solution
	}

	return "1. 及时更新相关组件到最新版本\n2. 实施安全编码规范\n3. 定期进行安全测试\n4. 关注安全公告和补丁"
}

func getString(m map[string]interface{}, key string) string {
	if val, ok := m[key]; ok {
		if str, ok := val.(string); ok {
			return str
		}
		if val != nil {
			return fmt.Sprintf("%v", val)
		}
	}
	return ""
}

func sanitizeFilename(value string) string {
	re := regexp.MustCompile(`[^a-zA-Z0-9._-]+`)
	sanitized := re.ReplaceAllString(strings.TrimSpace(value), "_")
	sanitized = strings.Trim(sanitized, "._-")
	if sanitized == "" {
		return "target"
	}
	return sanitized
}

func (rg *ReportGenerator) ExportToJSON() (string, error) {
	data := rg.prepareData()
	jsonData, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(rg.outputDir, 0755); err != nil {
		return "", err
	}

	timestamp := time.Now().Format("20060102_150405")
	filename := fmt.Sprintf("report_%s_%s.json", sanitizeFilename(rg.target), timestamp)
	filepath := filepath.Join(rg.outputDir, filename)

	if err := os.WriteFile(filepath, jsonData, 0644); err != nil {
		return "", err
	}

	return filepath, nil
}
