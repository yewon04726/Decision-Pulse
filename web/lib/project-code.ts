/** 프로젝트의 짧은 식별자: 두 단어 이상이면 앞 두 단어의 머리글자, 한 단어면 앞 두 글자 (예: Decision Debt MVP → DD) */
export function projectCode(name: string) {
  const words = name.trim().split(/[\s_-]+/).filter(Boolean);
  const letters = words.length > 1 ? words.slice(0, 2).map((w) => [...w][0]) : [...(words[0] ?? "?")].slice(0, 2);
  return letters.join("").toUpperCase();
}
