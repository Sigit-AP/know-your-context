# define_method, method_missing and respond_to?.
class Dynamic
  def method_missing(name, *args)
    return args.first if name.to_s.start_with?("get_")

    super
  end

  def respond_to_missing?(name, include_private = false)
    name.to_s.start_with?("get_") || super
  end
end

class Built
  define_method(:answer) { 42 }
end
