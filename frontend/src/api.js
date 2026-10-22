async function getJson(path) {
  const response = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      detail = (await response.json()).detail || detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return response.json()
}

export const api = {
  listPatients: () => getJson('/api/patients/'),
  getPatient: (id) => getJson(`/api/patients/${id}/`),
}
