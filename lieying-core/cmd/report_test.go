package cmd

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kunlun-sec/lunying/pkg/scan"
)

func TestReportCommandGeneratesMarkdownFromScanJSON(t *testing.T) {
	tempDir := t.TempDir()
	inputPath := filepath.Join(tempDir, "scan.json")

	payload := &scan.ScanResult{
		Target: "https://example.edu",
		SQLiResults: []scan.SQLiResult{{
			URL:       "https://example.edu/?id=1",
			Parameter: "id",
			Type:      "Error-based SQL Injection",
			Payload:   "'",
			Evidence:  "sql syntax",
			Severity:  "High",
		}},
	}
	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("Marshal() error = %v", err)
	}
	if err := os.WriteFile(inputPath, data, 0644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}

	cmd := exec.Command("go", "run", "./src/main.go", "report", "src", "--input", inputPath, "--stdout")
	cmd.Dir = filepath.Join("..")
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("report command error = %v\noutput:\n%s", err, string(output))
	}

	stdout := string(output)
	checks := []string{
		"# 渗透测试报告 - https://example.edu",
		"## 漏洞详情",
		"### VULN-001 - Error-based SQL Injection",
	}
	for _, check := range checks {
		if !strings.Contains(stdout, check) {
			t.Fatalf("expected output to contain %q, got:\n%s", check, stdout)
		}
	}
}
