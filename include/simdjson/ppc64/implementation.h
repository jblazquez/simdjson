#ifndef SIMDJSON_PPC64_IMPLEMENTATION_H
#define SIMDJSON_PPC64_IMPLEMENTATION_H

#ifndef SIMDJSON_AMALGAMATED
#include "simdjson/ppc64/base.h"
#include "simdjson/implementation.h"
#include "simdjson/internal/instruction_set.h"
#endif // SIMDJSON_AMALGAMATED

namespace simdjson {

/**
 * Implementation for ALTIVEC (PPC64).
 */
namespace ppc64 {

/**
 * @private
 */
class implementation final : public simdjson::implementation {
public:
  simdjson_inline implementation()
      : simdjson::implementation("ppc64", "PPC64 ALTIVEC",
                                 internal::instruction_set::ALTIVEC) {}

  simdjson_warn_unused error_code create_dom_parser_implementation(
      size_t capacity, size_t max_length,
      std::unique_ptr<internal::dom_parser_implementation> &dst)
      const noexcept final;
  simdjson_warn_unused error_code minify(const uint8_t *buf, size_t len,
                                         uint8_t *dst,
                                         size_t &dst_len) const noexcept final;
  simdjson_warn_unused bool validate_utf8(const char *buf,
                                          size_t len) const noexcept final;
};

} // namespace ppc64
} // namespace simdjson

#endif // SIMDJSON_PPC64_IMPLEMENTATION_H
