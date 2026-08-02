import type { LayerDef } from './layers'

export type PickedFeature = {
  layer: LayerDef
  props: Record<string, unknown>
}
