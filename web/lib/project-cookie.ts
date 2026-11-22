export const PROJECT_COOKIE = "dd_project";

/** 클라이언트에서 현재 프로젝트를 바꾼다. 호출 후 router.refresh()로 서버 컴포넌트를 다시 그린다. */
export function setProjectCookie(projectId: number) {
  document.cookie = `${PROJECT_COOKIE}=${projectId}; path=/; max-age=31536000; samesite=lax`;
}
