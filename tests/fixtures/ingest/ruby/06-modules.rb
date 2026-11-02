# Modules as namespaces and mixin containers.
module Geometry
  PI_APPROX = 3.14

  def self.distance(a, b)
    (a - b).abs
  end

  module Deep
    def self.value
      1
    end
  end
end

module Taggable
  def tags
    @tags ||= []
  end
end
