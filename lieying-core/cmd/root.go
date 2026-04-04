package cmd

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

var rootCmd = &cobra.Command{
	Use:   "lieying",
	Short: "猎影渗透测试平台 CLI工具",
	Long: `猎影渗透测试平台 - CLI工具
昆仑安全实验室(前逍遥安全实验室-逍遥)出品

一个专业的渗透测试工具集，支持信息收集、漏洞扫描、AI辅助等功能。`,
}

func Execute() {
	if err := rootCmd.Execute(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func init() {
	rootCmd.AddCommand(reconCmd)
	rootCmd.AddCommand(scanCmd)
	rootCmd.AddCommand(reportCmd)
	rootCmd.AddCommand(aiCmd)
	rootCmd.AddCommand(eduCmd)
	rootCmd.AddCommand(configCmd)
	rootCmd.AddCommand(serverCmd)
	rootCmd.AddCommand(versionCmd)
}
