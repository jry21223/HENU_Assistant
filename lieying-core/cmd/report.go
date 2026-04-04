package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/kunlun-sec/lunying/pkg/report"
	"github.com/kunlun-sec/lunying/pkg/scan"
	"github.com/spf13/cobra"
)

var (
	reportInputPath  string
	reportOutputPath string
	reportStdout     bool
)

var reportCmd = &cobra.Command{
	Use:   "report",
	Short: "生成漏洞报告",
}

var reportSrcCmd = &cobra.Command{
	Use:   "src",
	Short: "根据扫描 JSON 生成 SRC 风格报告",
	Run: func(cmd *cobra.Command, args []string) {
		if reportInputPath == "" {
			fmt.Fprintln(os.Stderr, "必须指定 --input")
			os.Exit(1)
		}
		if !reportStdout && reportOutputPath == "" {
			fmt.Fprintln(os.Stderr, "必须指定 --stdout 或 --output")
			os.Exit(1)
		}

		data, err := os.ReadFile(reportInputPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "读取扫描结果失败: %v\n", err)
			os.Exit(1)
		}

		var result scan.ScanResult
		if err := json.Unmarshal(data, &result); err != nil {
			fmt.Fprintf(os.Stderr, "解析扫描结果失败: %v\n", err)
			os.Exit(1)
		}

		markdown, err := report.GenerateMarkdownFromScanResult(&result, "src")
		if err != nil {
			fmt.Fprintf(os.Stderr, "生成报告失败: %v\n", err)
			os.Exit(1)
		}

		if reportStdout {
			fmt.Print(markdown)
		}
		if reportOutputPath != "" {
			if err := os.MkdirAll(filepath.Dir(reportOutputPath), 0755); err != nil {
				fmt.Fprintf(os.Stderr, "创建报告目录失败: %v\n", err)
				os.Exit(1)
			}
			if err := os.WriteFile(reportOutputPath, []byte(markdown), 0644); err != nil {
				fmt.Fprintf(os.Stderr, "写入报告失败: %v\n", err)
				os.Exit(1)
			}
			fmt.Printf("报告已保存到: %s\n", reportOutputPath)
		}
	},
}

func init() {
	reportSrcCmd.Flags().StringVar(&reportInputPath, "input", "", "扫描结果 JSON 文件路径")
	reportSrcCmd.Flags().StringVarP(&reportOutputPath, "output", "o", "", "Markdown 报告输出路径")
	reportSrcCmd.Flags().BoolVar(&reportStdout, "stdout", false, "输出 Markdown 到标准输出")
	reportCmd.AddCommand(reportSrcCmd)
}
