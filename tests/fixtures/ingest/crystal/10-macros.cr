# Macros as compile-time definitions.
annotation Tagged
end

@[Tagged]
class Service
  def handle : String
    "handled"
  end
end

def dispatch : String
  Service.new.handle
end
