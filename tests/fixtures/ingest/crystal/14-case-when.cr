# case/when with types and ranges.
def classify(value : Int32 | String | Nil) : String
  case value
  when String
    "string:#{value.size}"
  when Int32
    case value
    when 0..9   then "single"
    when 10..99 then "double"
    else             "big"
    end
  when Nil
    "nil"
  else
    "unknown"
  end
end
