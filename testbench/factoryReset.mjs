/**
 * Run the testbench factory-reset contract through the public management API.
 * The index is a derived store, so clear it only after the transactional memory reset succeeds.
 */
export async function resetTestbenchSubject({ memoryApi, retriever, subjectId }) {
  const counts = memoryApi.resetSubject({ subjectId });
  await retriever.indexAll([]);
  return counts;
}
