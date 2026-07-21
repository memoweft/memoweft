/**
 * Select the complete portable-memory dependency surface from one open StoreBundle.
 * Keep export and import wired to the same helper so new portable layers cannot be omitted from
 * one route while present in the other.
 */
export function portableDeps(stores) {
  return {
    evidenceStore: stores.evidenceStore,
    eventStore: stores.eventStore,
    cognitionStore: stores.cognitionStore,
    interactionContextStore: stores.interactionContextStore,
    semanticResolutionStore: stores.semanticResolutionStore,
    transaction: stores.transaction,
  };
}
