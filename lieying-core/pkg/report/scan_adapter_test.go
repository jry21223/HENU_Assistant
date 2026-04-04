package report

import (
	"strings"
	"testing"

	"github.com/kunlun-sec/lunying/pkg/scan"
)

func TestScanResultToReportVulnsFlattensScannerResults(t *testing.T) {
	result := &scan.ScanResult{
		Target: "https://example.edu",
		NucleiResults: []scan.NucleiResult{
			{
				TemplateID: "tpl-1",
				Matched:    "https://example.edu/login",
				Info: scan.NucleiInfo{
					Name:        "Exposed Panel",
					Severity:    "critical",
					Description: "public admin panel exposed",
				},
				Extracted: "banner=admin",
			},
		},
		SQLiResults: []scan.SQLiResult{{
			URL:       "https://example.edu/?id=1",
			Parameter: "id",
			Type:      "Error-based SQL Injection",
			Payload:   "'",
			Evidence:  "sql syntax",
			Severity:  "High",
		}},
		UnauthResults: []scan.UnauthResult{{
			URL:        "https://example.edu/admin",
			Path:       "/admin",
			Type:       "Unauthorized Admin Access",
			Evidence:   "后台页面存在管理功能特征且无需登录即可访问",
			Severity:   "Critical",
			Confirmed:  true,
			StatusCode: 200,
		}},
	}

	vulns := ScanResultToReportVulns(result)
	if len(vulns) != 3 {
		t.Fatalf("expected 3 vulnerabilities, got %d", len(vulns))
	}

	if got := vulns[0]["name"]; got != "Exposed Panel" {
		t.Fatalf("expected nuclei name, got %v", got)
	}
	if got := vulns[0]["severity"]; got != "Critical" {
		t.Fatalf("expected normalized nuclei severity, got %v", got)
	}
	if got := vulns[1]["type"]; got != "SQL注入" {
		t.Fatalf("expected SQLi type mapping, got %v", got)
	}
	if got := vulns[2]["name"]; got != "Unauthorized Admin Access" {
		t.Fatalf("expected unauth name fallback, got %v", got)
	}
	if got := vulns[2]["url"]; got != "https://example.edu/admin" {
		t.Fatalf("expected unauth URL, got %v", got)
	}
}

func TestGenerateMarkdownFromScanResultIncludesSummaryAndDetails(t *testing.T) {
	result := &scan.ScanResult{
		Target: "https://example.edu",
		XSSResults: []scan.XSSResult{{
			URL:       "https://example.edu/search?q=test",
			Parameter: "q",
			Type:      "Reflected XSS",
			Payload:   "<script>alert(1)</script>",
			Evidence:  "Payload 以未转义 HTML/JS 上下文反射",
			Severity:  "High",
		}},
	}

	markdown, err := GenerateMarkdownFromScanResult(result, "src")
	if err != nil {
		t.Fatalf("GenerateMarkdownFromScanResult() error = %v", err)
	}

	checks := []string{
		"# 渗透测试报告 - https://example.edu",
		"## 漏洞摘要",
		"### VULN-001 - Reflected XSS",
		"**漏洞类型**: XSS",
		"**漏洞URL**: https://example.edu/search?q=test",
	}
	for _, check := range checks {
		if !strings.Contains(markdown, check) {
			t.Fatalf("expected markdown to contain %q, got:\n%s", check, markdown)
		}
	}
}
