# Modules as namespaces and mixins.
module Geometry
  PI_APPROX = 3.14

  def self.distance(a : Int32, b : Int32) : Int32
    (a - b).abs
  end

  module Deep
    def self.value : Int32
      1
    end
  end
end

module Taggable
  def tags : Array(String)
    @tags ||= [] of String
  end
end
