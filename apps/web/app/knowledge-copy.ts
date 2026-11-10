/** Display summaries for bundled sample text; leave uploaded source text intact. */
const sampleSummaries: Record<string, string> = {
  "SYNTHETIC. Al-6061-T6 mounting plate; supplier availability pending. 材料目录":
    "Al-6061-T6 安装板材料目录，供应商库存待确认。",
  "SYNTHETIC. Blank preparation, milling/drilling, anodize, inspection. 工艺模板":
    "工艺路线：备料、铣削与钻孔、阳极氧化、检验。",
  "SYNTHETIC. Current prices only from manufacturing.json. 有效费率":
    "德国工厂制造费率，币种 EUR。",
  "SYNTHETIC. Historical unit price EUR 9.99. Prior batch assumptions differ; never reuse this price.":
    "历史单价 EUR 9.99，批量条件与当前询价不同。",
};

export function knowledgeSummary(content: string): string {
  return sampleSummaries[content] ?? content;
}

export const knowledgeReasons: Record<string, string> = {
  UNAPPROVED: "尚未批准",
  OUTSIDE_VALIDITY: "不在有效期内",
  REFERENCE_ONLY: "历史资料",
  MISMATCH_MATERIAL: "材料不适用",
  MISMATCH_REVISION: "版次不适用",
  MISMATCH_PART_NUMBER: "零件不适用",
  MISMATCH_UNIT: "单位不适用",
  MISMATCH_CURRENCY: "币种不适用",
  PAYLOAD_VERSION_MISMATCH: "资料版本不一致",
  INVALID_MANUFACTURING_PAYLOAD: "制造资料无效或过期",
  PAYLOAD_NOT_APPLICABLE: "制造资料不适用于当前条件",
};
