package report

import (
	"fmt"
	"strings"

	"github.com/kunlun-sec/lunying/pkg/scan"
)

func ScanResultToReportVulns(result *scan.ScanResult) []map[string]interface{} {
	if result == nil {
		return nil
	}

	vulns := make([]map[string]interface{}, 0, result.TotalVulns)

	for _, item := range result.NucleiResults {
		vulns = append(vulns, map[string]interface{}{
			"name":        fallback(item.Info.Name, item.TemplateID, "Nuclei Finding"),
			"type":        fallback(mapNucleiType(item.Type), "信息泄露"),
			"severity":    normalizeSeverity(item.Info.Severity),
			"url":         fallback(item.Matched, item.Host, result.Target),
			"description": fallback(item.Info.Description, fmt.Sprintf("Nuclei 模板 %s 命中目标", fallback(item.TemplateID, item.Template, "unknown-template"))),
			"evidence":    fallback(item.Extracted, item.TemplateURL, item.TemplatePath, item.TemplateID),
			"poc":         fallback(item.TemplateURL, item.TemplatePath, item.TemplateID),
		})
	}

	for _, item := range result.SQLiResults {
		vulns = append(vulns, map[string]interface{}{
			"name":        fallback(item.Type, "SQL Injection"),
			"type":        "SQL注入",
			"severity":    normalizeSeverity(item.Severity),
			"url":         fallback(item.URL, result.Target),
			"description": fmt.Sprintf("参数 %s 存在 %s 风险", fallback(item.Parameter, "unknown"), fallback(item.Type, "SQL注入")),
			"evidence":    item.Evidence,
			"poc":         item.Payload,
		})
	}

	for _, item := range result.XSSResults {
		vulns = append(vulns, map[string]interface{}{
			"name":        fallback(item.Type, "XSS"),
			"type":        "XSS",
			"severity":    normalizeSeverity(item.Severity),
			"url":         fallback(item.URL, result.Target),
			"description": fmt.Sprintf("参数 %s 存在 %s 风险", fallback(item.Parameter, "unknown"), fallback(item.Type, "XSS")),
			"evidence":    item.Evidence,
			"poc":         item.Payload,
		})
	}

	for _, item := range result.UploadResults {
		vulns = append(vulns, map[string]interface{}{
			"name":        fallback(item.Type, "File Upload"),
			"type":        "文件上传",
			"severity":    normalizeSeverity(item.Severity),
			"url":         fallback(item.URL, item.FormAction, result.Target),
			"description": fmt.Sprintf("上传点 %s 接受危险文件上传", fallback(item.InputName, "file")),
			"evidence":    item.Evidence,
			"poc":         item.Payload,
		})
	}

	for _, item := range result.UnauthResults {
		vulns = append(vulns, map[string]interface{}{
			"name":        fallback(item.Type, "Unauthorized Access"),
			"type":        "未授权访问",
			"severity":    normalizeSeverity(item.Severity),
			"url":         fallback(item.URL, result.Target),
			"description": fmt.Sprintf("路径 %s 存在未授权访问风险", fallback(item.Path, item.URL)),
			"evidence":    item.Evidence,
			"poc":         fmt.Sprintf("HTTP %d", item.StatusCode),
		})
	}

	return vulns
}

func GenerateMarkdownFromScanResult(result *scan.ScanResult, template string) (string, error) {
	if result == nil {
		return "", fmt.Errorf("scan result is nil")
	}
	rg := NewReportGenerator(result.Target, ScanResultToReportVulns(result))
	if template != "" {
		rg.SetTemplate(template)
	}
	return rg.GenerateMarkdown()
}

func normalizeSeverity(severity string) string {
	switch strings.ToLower(strings.TrimSpace(severity)) {
	case "critical", "严重":
		return "Critical"
	case "high", "高危":
		return "High"
	case "medium", "中危":
		return "Medium"
	case "low", "低危":
		return "Low"
	default:
		return fallback(severity, "Medium")
	}
}

func mapNucleiType(raw string) string {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "xss":
		return "XSS"
	case "sqli", "sql", "sql injection":
		return "SQL注入"
	case "file-upload", "upload":
		return "文件上传"
	case "unauth", "unauthorized-access":
		return "未授权访问"
	default:
		return raw
	}
}

func fallback(values ...string) string {
	for _, value := range values {
		trimmed := strings.TrimSpace(value)
		if trimmed != "" {
			return trimmed
		}
	}
	return ""
}
