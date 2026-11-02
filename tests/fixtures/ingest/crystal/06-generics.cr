# Generic classes and methods.
class Box
  def initialize(@item : Int32)
  end

  def unwrap : Int32
    @item
  end
end

def identity(value : T) : T forall T
  value
end

def first(items : Array(T)) : T? forall T
  items.first?
end
