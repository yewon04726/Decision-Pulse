// 현재 선택된 프로젝트 (서버 컴포넌트 전용).
// 선택값은 쿠키에 저장하고, Project Selector가 쿠키를 바꾼 뒤 router.refresh()로 다시 렌더링한다.
// cookies()는 요청 시점 데이터이므로 이 함수를 쓰는 컴포넌트는 <Suspense> 안에 있어야 한다.

import { cookies } from "next/headers";
import { getProjects } from "./api";
import { PROJECT_COOKIE } from "./project-cookie";
import type { Project } from "./types";

export async function getCurrentProject(): Promise<{ project: Project; projects: Project[] }> {
  const [projects, store] = await Promise.all([getProjects(), cookies()]);
  if (projects.length === 0) throw new Error("프로젝트가 없습니다. backend DB 초기화를 확인하세요.");
  const selected = Number(store.get(PROJECT_COOKIE)?.value);
  // 쿠키가 없거나 존재하지 않는 프로젝트면 기본 프로젝트(첫 번째)
  const project = projects.find((p) => p.id === selected) ?? projects[0];
  return { project, projects };
}
