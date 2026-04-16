package main

import (
	"encoding/json"
	"encoding/xml"
	"fmt"
	"os"
	"strconv"
	"strings"
)

// WPE .fp XML 结构
type fpFilterList struct {
	Filters []fpFilter `xml:"Filter"`
}

type fpFilter struct {
	Name          string `xml:"Name"`
	IsEnable      string `xml:"IsEnable"`
	Action        string `xml:"Action"`
	Search        string `xml:"Search"`
	Modify        string `xml:"Modify"`
	AppointHeader string `xml:"AppointHeader"`
	HeaderContent string `xml:"HeaderContent"`
	AppointLength string `xml:"AppointLength"`
	LengthContent string `xml:"LengthContent"`
}

// ParseFPSearchModify 解析 "0|10,1|00,2|00" 格式
func ParseFPSearchModify(text string) []SearchModifyEntry {
	text = strings.TrimSpace(text)
	if text == "" {
		return nil
	}
	var result []SearchModifyEntry
	for _, item := range strings.Split(text, ",") {
		parts := strings.SplitN(strings.TrimSpace(item), "|", 2)
		if len(parts) != 2 {
			continue
		}
		pos, err1 := strconv.Atoi(parts[0])
		val, err2 := strconv.ParseInt(parts[1], 16, 32)
		if err1 == nil && err2 == nil {
			result = append(result, SearchModifyEntry{Pos: pos, Val: int(val)})
		}
	}
	return result
}

// RunFPConvert 转换 WPE .fp 文件为 JSON 并输出
func RunFPConvert(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}

	var fl fpFilterList
	if err := xml.Unmarshal(data, &fl); err != nil {
		return fmt.Errorf("XML 解析失败: %v", err)
	}

	var rules []map[string]interface{}
	for _, f := range fl.Filters {
		search := ParseFPSearchModify(f.Search)
		modify := ParseFPSearchModify(f.Modify)

		enabled := f.IsEnable != "False"
		action := strings.ToLower(f.Action)
		if action == "" {
			action = "replace"
		}

		rule := map[string]interface{}{
			"name":    f.Name,
			"enabled": enabled,
			"action":  action,
			"search":  search,
			"modify":  modify,
		}

		header := strings.TrimSpace(f.HeaderContent)
		if f.AppointHeader == "True" && header != "" {
			rule["header"] = strings.ReplaceAll(header, " ", "")
		}

		if f.AppointLength == "True" && f.LengthContent != "" {
			parts := strings.SplitN(f.LengthContent, "-", 2)
			if len(parts) == 2 {
				if min, err := strconv.Atoi(parts[0]); err == nil {
					rule["length_min"] = min
				}
				if max, err := strconv.Atoi(parts[1]); err == nil {
					rule["length_max"] = max
				}
			}
		}

		rules = append(rules, rule)
	}

	output := map[string]interface{}{"rules": rules}
	jsonData, err := json.MarshalIndent(output, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(jsonData))
	return nil
}
